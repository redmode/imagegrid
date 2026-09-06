"""Pure geometry. No Pillow, no reportlab, no I/O — so it stays cheap to test.

Everything physical is millimetres; everything in image space is integer pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .units import Orientation, px_to_mm

# Overflow below this counts as fitting. Neither a printer nor a hand-cut can resolve a
# tenth of a millimetre, and float arithmetic on dpi-derived sizes routinely lands a few
# hundredths over an exact fit.
FIT_TOLERANCE_MM = 0.1

# Guards ceil() against a size that is a whole number of sheets but lands a few ulps above it.
_CEIL_EPSILON = 1e-9

# Height reserved along the bottom of the printable area for the per-tile caption, big
# enough for the 7pt line plus its clearance. It is subtracted before the grid is chosen:
# auto-fit maximises the tile to minimise sheets, so any space not reserved up front gets
# eaten, and the caption would appear only when ceil() happened to leave a crumb behind.
CAPTION_STRIP_MM = 5.0

# Blank paper left on a tile's bottom and right edges, to slide under the neighbouring
# sheet and take the glue. It is not extra image, and the visible seam stays an exact butt
# joint, so the finished poster keeps its known size. Like the caption strip it is
# subtracted before the grid is chosen: taking it from whatever white happened to be left
# over meant a tile could end up flush against it, with no room to print the cut line.
GLUE_FLAP_MM = 5.0


class LayoutError(ValueError):
    """The requested layout is impossible."""


@dataclass(frozen=True)
class Tile:
    """One piece of the image. Pixel box is half-open: [x0, x1) x [y0, y1)."""

    row: int  # 0-based, top to bottom
    col: int  # 0-based, left to right
    x0: int
    y0: int
    x1: int
    y1: int
    width_mm: float
    height_mm: float
    # Flaps go on the bottom and right, so these say whether this tile needs one.
    has_neighbour_below: bool = False
    has_neighbour_right: bool = False

    @property
    def width_px(self) -> int:
        return self.x1 - self.x0

    @property
    def height_px(self) -> int:
        return self.y1 - self.y0

    @property
    def label(self) -> str:
        """Stable 1-based identifier used in filenames and on the printed page."""
        return f"r{self.row + 1:02d}c{self.col + 1:02d}"

    @property
    def box(self) -> tuple[int, int, int, int]:
        """Crop box in Pillow's (left, upper, right, lower) form."""
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass(frozen=True)
class Margins:
    """The border a printer cannot reach, per side, in CSS order.

    Sides are named for the page as it is looked at, so ``bottom`` stays the bottom in
    either orientation rather than following the physical paper-feed edge.
    """

    top: float
    right: float
    bottom: float
    left: float

    def __post_init__(self) -> None:
        for side in ("top", "right", "bottom", "left"):
            if getattr(self, side) < 0:
                raise LayoutError(f"{side} margin cannot be negative")

    @classmethod
    def uniform(cls, mm: float) -> Margins:
        return cls(mm, mm, mm, mm)

    @property
    def horizontal(self) -> float:
        return self.left + self.right

    @property
    def vertical(self) -> float:
        return self.top + self.bottom

    @property
    def is_uniform(self) -> bool:
        return self.top == self.right == self.bottom == self.left

    def __str__(self) -> str:
        if self.is_uniform:
            return f"margin {self.top:g} mm"
        return f"margins t{self.top:g} r{self.right:g} b{self.bottom:g} l{self.left:g} mm"


@dataclass(frozen=True)
class Page:
    """The sheet a tile is printed on, already resolved to a concrete orientation."""

    width_mm: float
    height_mm: float
    margins: Margins
    orientation: Orientation
    caption_strip_mm: float = 0.0
    flap_right_mm: float = 0.0
    flap_bottom_mm: float = 0.0

    @property
    def printable_width_mm(self) -> float:
        """What the printer can reach."""
        return self.width_mm - self.margins.horizontal

    @property
    def printable_height_mm(self) -> float:
        return self.height_mm - self.margins.vertical

    @property
    def content_width_mm(self) -> float:
        """What a tile may occupy: the printable area less everything reserved around it."""
        return self.printable_width_mm - self.flap_right_mm

    @property
    def content_height_mm(self) -> float:
        return self.printable_height_mm - self.caption_strip_mm - self.flap_bottom_mm

    def room_mm(self, tile_width_mm: float, tile_height_mm: float) -> dict[str, float]:
        """Printable white beyond each tile edge.

        The flap edges get their reserved strip on top of the band, which is what
        guarantees there is somewhere to print their cut line.
        """
        band_x, band_y = self.band_mm(tile_width_mm, tile_height_mm)
        return {
            "left": band_x,
            "right": band_x + self.flap_right_mm,
            "top": band_y,
            "bottom": band_y + self.flap_bottom_mm,
        }

    def tile_origin_mm(self, tile_width_mm: float, tile_height_mm: float) -> tuple[float, float]:
        """Bottom-left corner for a tile centred in the *content* area.

        Centring on the page instead would push image into the dead zone whenever the
        margins are asymmetric, and clipped pixels cannot be recovered at a butt joint.
        The caption strip sits above the content area and the bottom flap below it, so the
        tile clears both. The caption is at the top because the flap keeps part of the
        bottom border: a caption down there would end up under the glue, not in the offcut.
        """
        return (
            self.margins.left + (self.content_width_mm - tile_width_mm) / 2,
            self.margins.bottom
            + self.flap_bottom_mm
            + (self.content_height_mm - tile_height_mm) / 2,
        )

    def band_mm(self, tile_width_mm: float, tile_height_mm: float) -> tuple[float, float]:
        """White space between the tile edge and the content edge, per axis.

        Because the tile is centred in the content area this is the same on both sides of
        each axis, which is the room the cut marks live in. The caption has its own strip
        below and does not draw on this.
        """
        return (
            (self.content_width_mm - tile_width_mm) / 2,
            (self.content_height_mm - tile_height_mm) / 2,
        )

    def spare_for(self, tile_width_mm: float, tile_height_mm: float) -> tuple[float, float]:
        """Printable space left over around a tile of this size, per axis. Negative if it spills."""
        return (
            self.content_width_mm - tile_width_mm,
            self.content_height_mm - tile_height_mm,
        )


@dataclass(frozen=True)
class Plan:
    """Everything needed to cut and print, plus everything needed to explain it."""

    source_width_px: int
    source_height_px: int
    dpi: float
    dpi_source: str
    cols: int
    rows: int
    page: Page
    tiles: list[Tile]
    grid_is_auto: bool
    glue_flap_mm: float = 0.0
    auto_fit_note: str | None = None  # names a better grid when the user forced a worse one
    dpi_note: str | None = None  # how a requested print size was reconciled, if at all

    @property
    def width_mm(self) -> float:
        return px_to_mm(self.source_width_px, self.dpi)

    @property
    def height_mm(self) -> float:
        return px_to_mm(self.source_height_px, self.dpi)

    @property
    def sheet_count(self) -> int:
        return self.cols * self.rows

    @property
    def tile_width_mm(self) -> float:
        """Nominal tile width; individual tiles vary by well under a tenth of a mm."""
        return self.width_mm / self.cols

    @property
    def tile_height_mm(self) -> float:
        return self.height_mm / self.rows

    @property
    def spare_mm(self) -> tuple[float, float]:
        """Leftover printable space around the tile, per axis. Negative where it overflows."""
        return self.page.spare_for(self.tile_width_mm, self.tile_height_mm)

    @property
    def overflow_mm(self) -> tuple[float, float]:
        """How far the tile exceeds the printable area, per axis (0 if it fits)."""
        spare_x, spare_y = self.spare_mm
        return (max(0.0, -spare_x), max(0.0, -spare_y))

    @property
    def fits(self) -> bool:
        return max(self.overflow_mm) <= FIT_TOLERANCE_MM

    @property
    def room_mm(self) -> dict[str, float]:
        """Printable white beyond each edge of the nominal tile."""
        return self.page.room_mm(self.tile_width_mm, self.tile_height_mm)

    @property
    def flapped_edges(self) -> tuple[str, ...]:
        """Which edges carry a flap somewhere in the grid."""
        if self.glue_flap_mm <= 0:
            return ()
        return tuple(
            edge
            for edge, present in (("bottom", self.rows > 1), ("right", self.cols > 1))
            if present
        )


def split_boundaries(total_px: int, parts: int) -> list[int]:
    """Cut ``total_px`` into ``parts`` runs that partition it exactly.

    Returns ``parts + 1`` boundaries, always starting at 0 and ending at ``total_px``.
    Widths differ by at most one pixel, and nothing is lost to floor division — a
    3937 px wide image split 5 ways keeps all 3937 columns.
    """
    if parts < 1:
        raise LayoutError(f"cannot split into {parts} parts")
    if parts > total_px:
        raise LayoutError(f"cannot split {total_px} px into {parts} parts (fewer than 1 px each)")
    return [round(i * total_px / parts) for i in range(parts + 1)]


def build_tiles(width_px: int, height_px: int, cols: int, rows: int, dpi: float) -> list[Tile]:
    """Build the full grid of tiles in reading order (row 1 left-to-right, then row 2...)."""
    xs = split_boundaries(width_px, cols)
    ys = split_boundaries(height_px, rows)
    return [
        Tile(
            row=r,
            col=c,
            x0=xs[c],
            y0=ys[r],
            x1=xs[c + 1],
            y1=ys[r + 1],
            width_mm=px_to_mm(xs[c + 1] - xs[c], dpi),
            height_mm=px_to_mm(ys[r + 1] - ys[r], dpi),
            has_neighbour_below=r < rows - 1,
            has_neighbour_right=c < cols - 1,
        )
        for r in range(rows)
        for c in range(cols)
    ]


def auto_grid(
    width_mm: float, height_mm: float, printable_width_mm: float, printable_height_mm: float
) -> tuple[int, int]:
    """Fewest (cols, rows) whose tiles fit the printable area."""
    if printable_width_mm <= 0 or printable_height_mm <= 0:
        raise LayoutError("margins leave no printable area on the page")
    return (
        max(1, math.ceil(width_mm / printable_width_mm - _CEIL_EPSILON)),
        max(1, math.ceil(height_mm / printable_height_mm - _CEIL_EPSILON)),
    )


def page_for(
    paper_mm: tuple[float, float],
    orientation: Orientation,
    margins: Margins,
    caption_strip_mm: float = 0.0,
    flap_right_mm: float = 0.0,
    flap_bottom_mm: float = 0.0,
) -> Page:
    """Turn portrait paper dimensions into a concrete Page, validating the margins.

    Margins do not rotate: the page is turned, but ``bottom`` still means the bottom of
    the sheet as printed.
    """
    width_mm, height_mm = paper_mm
    if orientation is Orientation.landscape:
        width_mm, height_mm = height_mm, width_mm
    elif orientation is not Orientation.portrait:
        raise LayoutError(f"page orientation must be concrete, got {orientation.value!r}")
    if caption_strip_mm < 0:
        raise LayoutError("caption strip cannot be negative")
    if min(flap_right_mm, flap_bottom_mm) < 0:
        raise LayoutError("glue flap cannot be negative")
    for axis, used, extent in (
        ("left and right", margins.horizontal + flap_right_mm, width_mm),
        ("top and bottom", margins.vertical + caption_strip_mm + flap_bottom_mm, height_mm),
    ):
        if used >= extent:
            raise LayoutError(
                f"the {axis} margins, caption strip and glue flap total {used:g} mm, "
                f"leaving no room for a tile on {width_mm:g}x{height_mm:g} mm paper.\n"
                "Use a smaller --margin or --glue-flap, or --no-glue-flap."
            )
    return Page(
        width_mm=width_mm,
        height_mm=height_mm,
        margins=margins,
        orientation=orientation,
        caption_strip_mm=caption_strip_mm,
        flap_right_mm=flap_right_mm,
        flap_bottom_mm=flap_bottom_mm,
    )


def _candidate_pages(
    paper_mm: tuple[float, float],
    orientation: Orientation,
    margins: Margins,
    caption_strip_mm: float,
    flap_right_mm: float = 0.0,
    flap_bottom_mm: float = 0.0,
) -> list[Page]:
    """The page shapes worth considering: both for ``auto``, otherwise just the one asked for."""
    wanted = (
        [Orientation.portrait, Orientation.landscape]
        if orientation is Orientation.auto
        else [orientation]
    )
    return [
        page_for(paper_mm, o, margins, caption_strip_mm, flap_right_mm, flap_bottom_mm)
        for o in wanted
    ]


def _best_auto_grid(
    width_mm: float, height_mm: float, pages: list[Page]
) -> tuple[Page, tuple[int, int]]:
    """Pick the page and grid needing the fewest sheets; ties go to the roomiest fit.

    Shared by the auto-fit path and by the note shown alongside an explicit grid, so the
    two can never disagree about what auto-fit "would have done".
    """

    def rank(candidate: tuple[Page, tuple[int, int]]) -> tuple[int, float]:
        page, (cols, rows) = candidate
        spare_x, spare_y = page.spare_for(width_mm / cols, height_mm / rows)
        return (cols * rows, -(spare_x + spare_y))

    options = [
        (page, auto_grid(width_mm, height_mm, page.content_width_mm, page.content_height_mm))
        for page in pages
    ]
    return min(options, key=rank)


def _page_for_grid(pages: list[Page], tile_width_mm: float, tile_height_mm: float) -> Page:
    """Pick the orientation that best accommodates a tile size the caller has already fixed.

    Anything that fits beats anything that does not; among those that fit, the roomiest wins.
    When none fits we keep the closest miss so the error can report the smallest overflow.
    """

    def rank(page: Page) -> tuple[int, float]:
        spare_x, spare_y = page.spare_for(tile_width_mm, tile_height_mm)
        overflow = max(0.0, -spare_x) + max(0.0, -spare_y)
        return (0, -(spare_x + spare_y)) if overflow == 0 else (1, overflow)

    return min(pages, key=rank)


def plan_layout(
    *,
    width_px: int,
    height_px: int,
    dpi: float,
    dpi_source: str,
    dpi_note: str | None = None,
    paper_mm: tuple[float, float],
    margins: Margins,
    caption_strip_mm: float = 0.0,
    glue_flap_mm: float = 0.0,
    orientation: Orientation = Orientation.auto,
    grid: tuple[int, int] | None = None,
) -> Plan:
    """Resolve grid and page orientation together, since each constrains the other."""
    width_mm = px_to_mm(width_px, dpi)
    height_mm = px_to_mm(height_px, dpi)
    if grid is not None:
        cols, rows = grid
        if cols > width_px or rows > height_px:
            raise LayoutError(
                f"grid {cols}x{rows} is finer than the image is large ({width_px}x{height_px} px)"
            )

    def resolve(flap_right: float, flap_bottom: float):
        pages = _candidate_pages(
            paper_mm, orientation, margins, caption_strip_mm, flap_right, flap_bottom
        )
        if grid is None:
            return _best_auto_grid(width_mm, height_mm, pages), pages
        return (_page_for_grid(pages, width_mm / grid[0], height_mm / grid[1]), grid), pages

    # A flap is only wanted where a neighbour exists, so the grid has to be known before
    # the reservation can be sized. Reserving can only make a grid finer, never coarser,
    # so a single refinement settles it: an axis that needed a flap still does.
    (page, (cols, rows)), pages = resolve(0.0, 0.0)
    reserve_right = glue_flap_mm if cols > 1 else 0.0
    reserve_bottom = glue_flap_mm if rows > 1 else 0.0
    if reserve_right or reserve_bottom:
        (page, (cols, rows)), pages = resolve(reserve_right, reserve_bottom)

    note = (
        None
        if grid is None
        else _auto_fit_note(width_mm, height_mm, pages, chosen=(page, cols, rows))
    )

    return Plan(
        source_width_px=width_px,
        source_height_px=height_px,
        dpi=dpi,
        dpi_source=dpi_source,
        cols=cols,
        rows=rows,
        page=page,
        tiles=build_tiles(width_px, height_px, cols, rows, dpi),
        grid_is_auto=grid is None,
        glue_flap_mm=glue_flap_mm,
        auto_fit_note=note,
        dpi_note=dpi_note,
    )


def _auto_fit_note(
    width_mm: float,
    height_mm: float,
    pages: list[Page],
    chosen: tuple[Page, int, int],
) -> str | None:
    """Describe the auto-fit grid, unless it is the one the user already asked for."""
    best_page, (cols, rows) = _best_auto_grid(width_mm, height_mm, pages)
    chosen_page, chosen_cols, chosen_rows = chosen
    if (cols, rows) == (chosen_cols, chosen_rows) and best_page.orientation is (
        chosen_page.orientation
    ):
        return None
    return f"auto-fit would use {cols}x{rows} = {cols * rows} tiles ({best_page.orientation.value})"
