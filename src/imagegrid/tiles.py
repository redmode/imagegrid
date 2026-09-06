"""Cut the source image into tile files that carry their own print resolution.

Writing correct DPI metadata is the point: it is what makes "print at 100%" land at the
right physical size.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .layout import Plan, Tile

# These are deliberate, single-user, local-file operations on large photographs.
Image.MAX_IMAGE_PIXELS = None

#: A written tile paired with the file it landed in.
TileFile = tuple[Tile, Path]

# Guide-page proportions, all relative to the rendered guide so it scales with max_px.
_GUIDE_MAX_PX = 1600
_GUIDE_LINE_DIVISOR = 400  # grid line width
_GUIDE_FONT_DIVISOR = 45  # label size
_GUIDE_LABEL_PAD = 3  # plate padding, in line widths
_GUIDE_MIN_FONT_PX = 11

# Font candidates for the guide labels, most preferred first. The bitmap fallback is
# unreadable at poster scale, so it is worth trying the common platform faces.
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "DejaVuSans.ttf",
    "Arial.ttf",
)


def open_image(path: Path) -> Image.Image:
    image = Image.open(path)
    image.load()
    return image


def tile_path(out_dir: Path, stem: str, tile: Tile) -> Path:
    return out_dir / f"{stem}_{tile.label}.jpg"


def _save_options(image: Image.Image, dpi: float, quality: int) -> dict:
    # Pillow writes the JFIF density from `dpi`, which is what a print dialog reads.
    options: dict = {
        "dpi": (dpi, dpi),
        "quality": quality,
        "subsampling": 0,  # no chroma subsampling: these get inspected up close on paper
        "optimize": True,
        "progressive": bool(image.info.get("progressive") or image.info.get("progression")),
    }
    if icc := image.info.get("icc_profile"):
        options["icc_profile"] = icc
    return options


def write_tiles(
    image: Image.Image,
    plan: Plan,
    out_dir: Path,
    stem: str,
    quality: int,
) -> list[TileFile]:
    """Crop every tile and write it out as JPEG, tagged with the plan's print resolution."""
    source = image if image.mode in ("RGB", "L") else image.convert("RGB")
    options = _save_options(image, plan.dpi, quality)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[TileFile] = []
    for tile in plan.tiles:
        path = tile_path(out_dir, stem, tile)
        source.crop(tile.box).save(path, **options)
        written.append((tile, path))
    return written


def _label_font(size_px: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size_px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size_px)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def guide_image(image: Image.Image, plan: Plan, max_px: int = _GUIDE_MAX_PX) -> Image.Image:
    """A downscaled map of the whole image with the cut grid and tile labels drawn on.

    Printed as the PDF's first page so the assembly order is on the table while gluing.
    """
    scale = min(1.0, max_px / max(plan.source_width_px, plan.source_height_px))
    size = (
        max(1, round(plan.source_width_px * scale)),
        max(1, round(plan.source_height_px * scale)),
    )
    guide = image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(guide, "RGBA")

    line_px = max(1, round(min(size) / _GUIDE_LINE_DIVISOR))
    font = _label_font(max(_GUIDE_MIN_FONT_PX, round(min(size) / _GUIDE_FONT_DIVISOR)))
    pad = line_px * _GUIDE_LABEL_PAD

    for tile in plan.tiles:
        x0, y0 = tile.x0 * scale, tile.y0 * scale
        x1, y1 = tile.x1 * scale, tile.y1 * scale
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 255, 255, 230), width=line_px)
        # A dark plate behind the label keeps it readable over any photograph.
        centre = ((x0 + x1) / 2, (y0 + y1) / 2)
        box = draw.textbbox(centre, tile.label, font=font, anchor="mm")
        draw.rectangle(
            [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad], fill=(0, 0, 0, 150)
        )
        draw.text(centre, tile.label, font=font, fill=(255, 255, 255, 255), anchor="mm")

    return guide
