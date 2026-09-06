"""Command line entry point."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .layout import (
    CAPTION_STRIP_MM,
    GLUE_FLAP_MM,
    LayoutError,
    Margins,
    Plan,
    plan_layout,
)
from .pdf import build_pdf
from .resolution import PrintSize, ResolutionError, dpi_for_print_size, resolve_dpi
from .tiles import TileFile, guide_image, open_image, write_tiles
from .units import (
    Orientation,
    UnitError,
    parse_grid,
    parse_length,
    parse_paper,
    parse_size,
    px_to_mm,
)

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Split a large image into tiles that print at exact 100% scale.",
)
console = Console()
err_console = Console(stderr=True)

# Ignore a rounding drift smaller than this across the whole poster.
TILE_DPI_DRIFT_THRESHOLD_MM = 0.5


def _fail(message: str) -> NoReturn:
    err_console.print(f"[bold red]error[/]  {message}")
    raise typer.Exit(code=1)


def _plan_table(plan: Plan, image_path: Path, out_dir: Path, dry_run: bool = False) -> Table:
    table = Table(show_header=False, box=None, pad_edge=False, padding=(0, 2, 0, 0))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()

    source = (
        f"{plan.source_width_px}x{plan.source_height_px} px @ {plan.dpi:g} dpi "
        f"({plan.dpi_source})"
        f"  ->  [bold]{plan.width_mm:.1f} x {plan.height_mm:.1f} mm[/]"
    )
    if plan.dpi_note:
        source += f"\n[dim]{plan.dpi_note}[/]"
    table.add_row("Source", source)
    page = plan.page
    table.add_row(
        "Paper",
        f"{page.width_mm:g}x{page.height_mm:g} mm {page.orientation.value}, {page.margins}"
        f"  ->  printable {page.printable_width_mm:.0f} x {page.printable_height_mm:.0f} mm"
        + (
            f" [dim](less {page.caption_strip_mm:g} mm for captions)[/]"
            if page.caption_strip_mm
            else ""
        ),
    )
    grid = f"[bold]{plan.cols} columns x {plan.rows} rows = {plan.sheet_count} tiles[/]"
    if plan.grid_is_auto:
        grid += " [dim](auto-fit)[/]"
    elif plan.auto_fit_note:
        grid += f" [dim]({plan.auto_fit_note})[/]"
    table.add_row("Grid", grid)

    first = plan.tiles[0]
    table.add_row(
        "Tile",
        f"{first.width_px}x{first.height_px} px  ->  "
        f"{plan.tile_width_mm:.1f} x {plan.tile_height_mm:.1f} mm    {_fit_verdict(plan)}",
    )
    if plan.flapped_edges:
        edges = plan.flapped_edges
        table.add_row(
            "Glue",
            f"{plan.glue_flap_mm:g} mm flap on the {' and '.join(edges)} "
            f"{'edges' if len(edges) > 1 else 'edge'}"
            "  [dim](reserved before the grid, so its cut line always prints)[/]",
        )
    table.add_row("Input", str(image_path))
    suffix = "  [dim](dry run: nothing written)[/]" if dry_run else ""
    table.add_row("Output", f"{out_dir}{suffix}")
    return table


def _fit_verdict(plan: Plan) -> str:
    if plan.fits:
        spare_x, spare_y = plan.spare_mm
        return f"[green]fits[/], {max(0.0, spare_x):.1f} x {max(0.0, spare_y):.1f} mm to spare"
    over_x, over_y = plan.overflow_mm
    over = " and ".join(
        part
        for part in (
            f"{over_x:.1f} mm wide" if over_x else "",
            f"{over_y:.1f} mm tall" if over_y else "",
        )
        if part
    )
    return f"[bold red]does not fit[/] — over by {over}"


def _tile_dpi_warning(plan: Plan) -> str | None:
    """JPEG records JFIF density as a whole number, so a fractional dpi is lost on the tiles.

    The PDF places each tile at its exact size regardless, so this only bites someone
    printing the loose tile files directly — which is why it is only shown with --tiles.
    """
    tagged = round(plan.dpi)
    if tagged == plan.dpi or tagged <= 0:
        return None
    drift_mm = px_to_mm(plan.source_width_px, tagged) - plan.width_mm
    if abs(drift_mm) < TILE_DPI_DRIFT_THRESHOLD_MM:
        return None
    return (
        f"tiles are tagged {tagged} dpi, not {plan.dpi:g} — JPEG stores whole numbers only.\n"
        f"        Printed from the tile files the poster would come out {abs(drift_mm):.1f} mm "
        f"{'wider' if drift_mm > 0 else 'narrower'}; the PDF is unaffected.\n"
        f"        Pick a size that lands on a whole dpi if the loose tiles must be exact."
    )


def _fit_advice(plan: Plan) -> str:
    other = (
        Orientation.landscape
        if plan.page.orientation is Orientation.portrait
        else Orientation.portrait
    )
    return (
        f"Try a finer grid, --orientation {other.value}, a larger --paper, "
        f"or a smaller --margin (currently {plan.page.margins}). "
        "Omit --grid entirely to let imagegrid pick a grid that fits."
    )


def _resolve_margins(
    base: str,
    top: str | None,
    right: str | None,
    bottom: str | None,
    left: str | None,
) -> Margins:
    """``--margin`` sets every side; each ``--margin-<side>`` overrides just that one."""
    default = parse_length(base)
    return Margins(
        top=parse_length(top) if top is not None else default,
        right=parse_length(right) if right is not None else default,
        bottom=parse_length(bottom) if bottom is not None else default,
        left=parse_length(left) if left is not None else default,
    )


def _resolve_scale(
    source,
    dpi: float | None,
    size: str | None,
    width: str | None,
    height: str | None,
):
    """Decide the print scale from whichever option the user reached for.

    ``--dpi`` and the size options say the same thing two different ways, so only one may
    be given. With none of them, the image's own recorded resolution stands.
    """
    size_flags = {"--size": size, "--width": width, "--height": height}
    given = [flag for flag, value in size_flags.items() if value is not None]
    if dpi is not None and given:
        _fail(f"--dpi and {given[0]} both set the print scale; use one or the other")
    if size is not None and len(given) > 1:
        others = ", ".join(flag for flag in given if flag != "--size")
        _fail(f"--size already gives both axes; drop {others}")

    if not given:
        return resolve_dpi(source, dpi)

    if size is not None:
        width_mm, height_mm = parse_size(size)
    else:
        width_mm = parse_length(width) if width is not None else None
        height_mm = parse_length(height) if height is not None else None
    return dpi_for_print_size(
        source.width, source.height, PrintSize(width_mm, height_mm)
    )


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"imagegrid {__version__}")
        raise typer.Exit()


@contextmanager
def _tile_destination(out_dir: Path, keep: bool) -> Iterator[Path]:
    """Where the tile files go: the output directory, or a scratch dir if they aren't wanted.

    ``--no-tiles`` still needs the files on disk for the PDF to embed, but the user asked
    not to have them, so they never appear in the output directory at all.
    """
    if keep:
        yield out_dir
        return
    with tempfile.TemporaryDirectory(prefix="imagegrid-") as scratch:
        yield Path(scratch)


def _write_outputs(
    source,
    plan: Plan,
    out_dir: Path,
    stem: str,
    quality: int,
    keep_tiles: bool,
    want_guide: bool,
) -> None:
    """Cut the tiles and build the PDF."""
    out_dir.mkdir(parents=True, exist_ok=True)

    with _tile_destination(out_dir, keep_tiles) as tile_dir:
        with console.status(f"cutting {plan.sheet_count} tiles..."):
            written: list[TileFile] = write_tiles(source, plan, tile_dir, stem, quality)
        if keep_tiles:
            console.print(
                f"  [green]v[/] {len(written)} tiles  [dim]{out_dir}/{stem}_r01c01.jpg, ...[/]"
            )

        pdf_path = out_dir / f"{stem}_print.pdf"
        with tempfile.TemporaryDirectory(prefix="imagegrid-guide-") as scratch:
            guide_png = None
            if want_guide:
                guide_png = Path(scratch) / "guide.png"
                guide_image(source, plan).save(guide_png)
            with console.status("building PDF..."):
                warnings = build_pdf(plan, written, pdf_path, stem, guide_png)
        pages = plan.sheet_count + (1 if want_guide else 0)
        console.print(f"  [green]v[/] PDF, {pages} pages  [dim]{pdf_path}[/]")

    for warning in warnings:
        err_console.print(f"  [yellow]![/] {warning}")


@app.command()
def split(
    image: Annotated[Path, typer.Argument(
        exists=True, dir_okay=False, readable=True, help="Image to split.")],
    output: Annotated[Path | None, typer.Option(
        "--output", "-o", help="Output directory.  [default: ./out/<image name>]")] = None,
    grid: Annotated[str | None, typer.Option(
        "--grid", "-g", metavar="COLSxROWS",
        help="Force a grid, columns first, e.g. 4x5.  [default: auto-fit]")] = None,
    paper: Annotated[str, typer.Option(
        "--paper", "-p", help="Paper name (a4, a3, letter...) or a size like 210x297mm.")] = "a4",
    orientation: Annotated[Orientation, typer.Option(
        "--orientation", help="Page orientation.")] = Orientation.auto,
    margin: Annotated[str, typer.Option(
        "--margin", "-m", help="Unprintable border on all four sides.")] = "10mm",
    margin_top: Annotated[str | None, typer.Option(
        "--margin-top", help="Override --margin for the top edge.")] = None,
    margin_right: Annotated[str | None, typer.Option(
        "--margin-right", help="Override --margin for the right edge.")] = None,
    margin_bottom: Annotated[str | None, typer.Option(
        "--margin-bottom", help="Override --margin for the bottom edge.")] = None,
    margin_left: Annotated[str | None, typer.Option(
        "--margin-left", help="Override --margin for the left edge.")] = None,
    dpi: Annotated[float | None, typer.Option(
        "--dpi", help="Print at this resolution instead of the image's own.")] = None,
    size: Annotated[str | None, typer.Option(
        "--size", "-s", metavar="WxH",
        help="Print at this finished size, e.g. 100x80cm. Aspect is preserved, so this "
             "acts as a bounding box.")] = None,
    width: Annotated[str | None, typer.Option(
        "--width", help="Print this wide, e.g. 150cm; the height follows the aspect.")] = None,
    height: Annotated[str | None, typer.Option(
        "--height", help="Print this tall, e.g. 80cm; the width follows the aspect.")] = None,
    quality: Annotated[int, typer.Option(
        "--quality", "-q", min=1, max=100, help="JPEG quality.")] = 100,
    keep_tiles: Annotated[bool, typer.Option(
        "--tiles/--no-tiles",
        help="Also write the individual tile images. The PDF already embeds them, so "
             "they are only useful on their own.")] = False,
    want_guide: Annotated[bool, typer.Option(
        "--guide/--no-guide", help="Include the assembly guide as the PDF's first page.")] = True,
    glue_flap: Annotated[str, typer.Option(
        "--glue-flap", metavar="LENGTH",
        help="Blank paper left on the bottom and right edges to glue under the next "
             "sheet.")] = f"{GLUE_FLAP_MM:g}mm",
    no_glue_flap: Annotated[bool, typer.Option(
        "--no-glue-flap",
        help="Cut every edge exactly, with no flap to glue.")] = False,
    caption: Annotated[bool, typer.Option(
        "--caption/--no-caption",
        help="Label each sheet in the border. Reserves a strip, which can cost a sheet "
             "or two on some images.")] = True,
    dry_run: Annotated[bool, typer.Option(
        "--dry-run", "-n", help="Show the plan and write nothing.")] = False,
    version: Annotated[bool | None, typer.Option(
        "--version", callback=_version_callback, is_eager=True,
        help="Show the version and exit.")] = None,
) -> None:
    """Split IMAGE into a grid of tiles, each sized to print on one sheet at 100% scale."""
    try:
        paper_mm = parse_paper(paper)
        margins = _resolve_margins(
            margin, margin_top, margin_right, margin_bottom, margin_left
        )
        grid_spec = parse_grid(grid) if grid else None
        glue_flap_mm = 0.0 if no_glue_flap else parse_length(glue_flap)
    except (UnitError, LayoutError) as exc:
        _fail(str(exc))

    try:
        source = open_image(image)
    except OSError as exc:
        _fail(f"cannot read {image}: {exc}")

    try:
        resolution = _resolve_scale(source, dpi, size, width, height)
        plan = plan_layout(
            width_px=source.width,
            height_px=source.height,
            dpi=resolution.dpi,
            dpi_source=resolution.source,
            dpi_note=resolution.note,
            paper_mm=paper_mm,
            margins=margins,
            caption_strip_mm=CAPTION_STRIP_MM if caption else 0.0,
            glue_flap_mm=glue_flap_mm,
            orientation=orientation,
            grid=grid_spec,
        )
    except (ResolutionError, LayoutError, UnitError) as exc:
        _fail(str(exc))

    stem = image.stem
    out_dir = output if output is not None else Path("out") / stem

    console.print()
    console.print(_plan_table(plan, image, out_dir, dry_run=dry_run))
    console.print()

    if keep_tiles and (warning := _tile_dpi_warning(plan)):
        err_console.print(f"[yellow]note[/]  {warning}")
        console.print()

    if not plan.fits:
        _fail(
            "each tile is larger than the printable area of the sheet.\n"
            f"        {_fit_advice(plan)}"
        )
    if dry_run:
        return

    _write_outputs(source, plan, out_dir, stem, quality, keep_tiles, want_guide)

    console.print()
    console.print(
        "[bold]Print at 100% scale[/] (not 'fit to page'), cut each sheet on the corner marks, "
        f"then butt the {plan.sheet_count} pieces edge to edge."
    )
    console.print()


if __name__ == "__main__":
    app()
