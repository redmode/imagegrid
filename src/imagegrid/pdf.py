"""Assemble the tiles into a print-ready PDF.

A PDF is used rather than raw images because it pins the page size and the exact
physical placement of each tile, so "100% / actual size" in the print dialog is
unambiguous. Cut marks live in the white border, which gets trimmed away.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .layout import Margins, Plan, Tile
from .tiles import TileFile
from .units import mm_to_pt

# Cut marks: L-shaped hairlines set back from each image corner, into the trim border.
MARK_OFFSET_MM = 2.0  # gap between the image corner and the start of the mark
MARK_LENGTH_MM = 5.0
MARK_MIN_SPACE_MM = 1.5  # below this there is no room for a usable mark
MARK_MIN_GAP_MM = 1.0  # keep at least this much border beyond the mark
MARK_MIN_OFFSET_MM = 0.5
MARK_LINE_WIDTH_PT = 0.25
# The flap cut line, drawn full length so there is nothing to measure. The space for it is
# reserved before the grid is chosen, so it always has somewhere to go.
FLAP_RULE_DASH_PT = (3.0, 2.0)

# Per-tile caption, printed in the top border. Everything here is measured from the margin
# line inward, never from the paper edge: a printer cannot put ink in the outer few
# millimetres, so a caption placed near the edge simply never appears on paper even though
# it shows up fine in a PDF viewer. The top is used because a glue flap keeps part of the
# bottom border, and a caption there would end up under the glue instead of in the offcut.
LABEL_BASELINE_BELOW_MARGIN_MM = 4.0
LABEL_FONT = ("Helvetica", 7)
LABEL_GREY = 0.45

# Assembly guide page.
GUIDE_TITLE_FONT = ("Helvetica-Bold", 12)
GUIDE_BODY_FONT = ("Helvetica", 8.5)
GUIDE_TITLE_DROP_PT = 12  # below the top margin
GUIDE_SUBTITLE_DROP_PT = 26
GUIDE_IMAGE_TOP_DROP_PT = 40
GUIDE_LEADING_PT = 11  # between wrapped footer lines
GUIDE_FOOTER_GAP_PT = 6  # between the footer text and the map above it
GUIDE_BODY_GREY = 0.35

PRINT_INSTRUCTION = (
    "Print every page at 100% (no 'fit to page'), cut each sheet on the corner marks, "
    "then butt the pieces edge to edge."
)
GLUE_LEGEND = (
    "Solid corner marks are cut lines. A dashed line is a glue edge: cut along it and "
    "slide that blank {flap:g} mm flap under the next sheet. Where a sheet is too tight to "
    "print the dashed line, its corner marks are dashed instead and you cut {flap:g} mm "
    "beyond them. Work outward from r01c01."
)


def _mark_geometry(band_mm: float) -> tuple[float, float] | None:
    """Offset and length in points for a cut mark, measured inward from the image edge.

    ``band_mm`` is the printable white space between the tile and the margin. Beyond the
    margin the printer cannot lay ink down, so a mark drawn there would be silently
    truncated on paper even though it looks right in a PDF viewer.

    Any band wide enough to clear ``MARK_MIN_SPACE_MM`` leaves at least
    ``MARK_MIN_GAP_MM`` for the arm itself, so a band either yields a usable mark or none
    at all — see ``test_the_constants_cannot_produce_a_useless_arm``.
    """
    if band_mm < MARK_MIN_SPACE_MM:
        return None
    offset = min(MARK_OFFSET_MM, max(MARK_MIN_OFFSET_MM, band_mm - MARK_MIN_GAP_MM))
    length = min(MARK_LENGTH_MM, band_mm - offset)
    return mm_to_pt(offset), mm_to_pt(length)


def flap_rule_segments(frame: CutFrame) -> list[tuple[float, float, float, float]]:
    """The flap's own cut line: the bottom and right edges of the piece itself.

    Drawing the line the user actually cuts on beats marking the image edge and asking them
    to measure outward, and because the two segments are edges of one rectangle they meet
    at the outer corner and trace the whole piece.
    """
    right, top = frame.x + frame.width, frame.y + frame.height
    segments = []
    if "bottom" in frame.glue:
        segments.append((frame.x, frame.y, right, frame.y))
    if "right" in frame.glue:
        segments.append((right, frame.y, right, top))
    return segments


def _draw_flap_rule(pdf: canvas.Canvas, segments: list[tuple[float, float, float, float]]) -> None:
    if not segments:
        return
    pdf.saveState()
    pdf.setLineWidth(MARK_LINE_WIDTH_PT)
    pdf.setStrokeColorRGB(0, 0, 0)
    pdf.setDash(*FLAP_RULE_DASH_PT)
    for x0, y0, x1, y1 in segments:
        pdf.line(x0, y0, x1, y1)
    pdf.restoreState()


@dataclass(frozen=True)
class CutFrame:
    """The rectangle actually cut out of the sheet, and the room around each of its edges.

    This is the image plus whatever glue flap is being kept, so its corners are the corners
    of the finished piece. Marking the image instead would leave the corner marks two
    edges short of where the blade goes on any sheet carrying a flap.
    """

    x: float  # points, bottom-left of the piece
    y: float
    width: float
    height: float
    room_mm: dict[str, float]  # printable white beyond each edge of the piece
    glue: frozenset[str]  # edges that are glue flaps rather than plain cuts


def cut_frame(
    x: float,
    y: float,
    width: float,
    height: float,
    room_mm: dict[str, float],
    flap_mm: float,
    glue_edges: tuple[str, ...],
) -> CutFrame:
    """Grow the image rectangle by every flap it keeps, giving the piece that gets cut out.

    The room beyond each grown edge shrinks by whatever the flap spent, which is what keeps
    the corner marks inside the printable area.
    """
    glue = frozenset(glue_edges)
    spent = {edge: (flap_mm if edge in glue else 0.0) for edge in ("right", "bottom")}
    return CutFrame(
        x=x,
        y=y - mm_to_pt(spent["bottom"]),
        width=width + mm_to_pt(spent["right"]),
        height=height + mm_to_pt(spent["bottom"]),
        room_mm={edge: room_mm[edge] - spent.get(edge, 0.0) for edge in room_mm},
        glue=glue,
    )


def _draw_crop_marks(pdf: canvas.Canvas, frame: CutFrame) -> bool:
    """L-shaped marks at each corner of the piece, pointing outward into the trim border.

    Each arm is collinear with the edge it marks — a horizontal arm sits at the height of
    the top or bottom edge, a vertical one at the left or right edge — so an arm is dashed
    is solid: every one of them sits on the real cut line. Arm length is taken per side,
    because a flap has already spent part of the room on the edges that carry it.
    """
    drawn = False
    pdf.saveState()
    pdf.setLineWidth(MARK_LINE_WIDTH_PT)
    pdf.setStrokeColorRGB(0, 0, 0)
    for corner_x, dx, side in ((frame.x, -1, "left"), (frame.x + frame.width, 1, "right")):
        for corner_y, dy, level in (
            (frame.y, -1, "bottom"),
            (frame.y + frame.height, 1, "top"),
        ):
            horizontal = _mark_geometry(frame.room_mm[side])
            vertical = _mark_geometry(frame.room_mm[level])
            if horizontal is not None:  # arm level with the top/bottom edge
                offset, length = horizontal
                pdf.line(
                    corner_x + dx * offset,
                    corner_y,
                    corner_x + dx * (offset + length),
                    corner_y,
                )
                drawn = True
            if vertical is not None:  # arm in line with the left/right edge
                offset, length = vertical
                pdf.line(
                    corner_x,
                    corner_y + dy * offset,
                    corner_x,
                    corner_y + dy * (offset + length),
                )
                drawn = True
    pdf.restoreState()
    return drawn


def _draw_label(
    pdf: canvas.Canvas, page_width: float, page_height: float, margins: Margins, text: str
) -> None:
    """Caption centred in the top border, just inside the margin so it actually prints."""
    pdf.saveState()
    pdf.setFont(*LABEL_FONT)
    pdf.setFillColorRGB(LABEL_GREY, LABEL_GREY, LABEL_GREY)
    baseline = page_height - mm_to_pt(margins.top + LABEL_BASELINE_BELOW_MARGIN_MM)
    pdf.drawCentredString(page_width / 2, baseline, text)
    pdf.restoreState()


def _tile_caption(plan: Plan, tile: Tile, stem: str) -> str:
    parts = [
        tile.label,
        f"{tile.width_mm:.1f} x {tile.height_mm:.1f} mm",
        f"col {tile.col + 1}/{plan.cols}  row {tile.row + 1}/{plan.rows}",
    ]
    edges = _glue_edges(tile, plan)
    if edges:
        parts.append(f"glue {plan.glue_flap_mm:g} mm: {' and '.join(edges)}")
    parts.append(stem)
    return "  ·  ".join(parts)


def _glue_edges(tile: Tile, plan: Plan) -> tuple[str, ...]:
    """The edges of this particular tile that carry a flap rather than a cut line."""
    if plan.glue_flap_mm <= 0:
        return ()
    return tuple(
        edge
        for edge, flapped in (
            ("bottom", tile.has_neighbour_below),
            ("right", tile.has_neighbour_right),
        )
        if flapped
    )


def _wrap(text: str, font: tuple[str, float], width: float) -> list[str]:
    """Break text into lines that fit ``width``, so a legend cannot run off the page."""
    lines: list[str] = []
    words = text.split()
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and stringWidth(candidate, *font) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _draw_footer(
    pdf: canvas.Canvas, paragraphs: list[str], left: float, bottom: float, width: float
) -> float:
    """Draw wrapped paragraphs upward from ``bottom``; return the height they occupy."""
    lines = [line for text in paragraphs for line in _wrap(text, GUIDE_BODY_FONT, width)]
    for index, line in enumerate(reversed(lines)):
        pdf.drawString(left, bottom + index * GUIDE_LEADING_PT, line)
    return len(lines) * GUIDE_LEADING_PT


def _draw_guide_page(
    pdf: canvas.Canvas,
    plan: Plan,
    guide_png: Path,
    stem: str,
    page_width: float,
    page_height: float,
) -> None:
    """A map of the finished poster, so the pieces can be laid out in the right order."""
    margins = plan.page.margins
    left, right = mm_to_pt(margins.left), mm_to_pt(margins.right)
    top, bottom_margin = mm_to_pt(margins.top), mm_to_pt(margins.bottom)

    pdf.setFont(*GUIDE_TITLE_FONT)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.drawString(left, page_height - top - GUIDE_TITLE_DROP_PT, f"{stem} — assembly guide")

    pdf.setFont(*GUIDE_BODY_FONT)
    pdf.setFillColorRGB(GUIDE_BODY_GREY, GUIDE_BODY_GREY, GUIDE_BODY_GREY)
    subtitle = (
        f"{plan.cols} columns x {plan.rows} rows = {plan.sheet_count} sheets  ·  "
        f"finished size {plan.width_mm:.0f} x {plan.height_mm:.0f} mm  ·  "
        f"tile {plan.tile_width_mm:.1f} x {plan.tile_height_mm:.1f} mm  ·  "
        f"{plan.dpi:g} dpi"
    )
    available_width = page_width - left - right
    pdf.drawString(left, page_height - top - GUIDE_SUBTITLE_DROP_PT, subtitle)

    paragraphs = [PRINT_INSTRUCTION]
    if plan.glue_flap_mm > 0:
        paragraphs.append(GLUE_LEGEND.format(flap=plan.glue_flap_mm))
    footer_height = _draw_footer(pdf, paragraphs, left, bottom_margin, available_width)

    image_top = page_height - top - GUIDE_IMAGE_TOP_DROP_PT
    image_bottom = bottom_margin + footer_height + GUIDE_FOOTER_GAP_PT
    available_height = image_top - image_bottom
    scale = min(available_width / plan.source_width_px, available_height / plan.source_height_px)
    width, height = plan.source_width_px * scale, plan.source_height_px * scale
    pdf.drawImage(
        ImageReader(str(guide_png)),
        left + (available_width - width) / 2,
        image_bottom + (available_height - height) / 2,
        width=width,
        height=height,
    )
    pdf.showPage()


def build_pdf(
    plan: Plan,
    tiles: list[TileFile],
    out_path: Path,
    stem: str,
    guide_png: Path | None = None,
) -> list[str]:
    """Write one page per tile (plus an optional guide page). Returns any warnings."""
    page_width, page_height = mm_to_pt(plan.page.width_mm), mm_to_pt(plan.page.height_mm)

    pdf = canvas.Canvas(str(out_path), pagesize=(page_width, page_height))
    pdf.setTitle(f"{stem} — {plan.cols}x{plan.rows} tiles")
    pdf.setSubject(
        f"{plan.width_mm:.0f} x {plan.height_mm:.0f} mm at {plan.dpi:g} dpi; print at 100% scale"
    )

    if guide_png is not None:
        _draw_guide_page(pdf, plan, guide_png, stem, page_width, page_height)

    marks_drawn = False
    for tile, path in tiles:
        # Each tile is placed at its own exact size rather than a nominal one, so the
        # 1 px rounding differences between tiles never accumulate across the poster.
        width, height = mm_to_pt(tile.width_mm), mm_to_pt(tile.height_mm)
        origin_x_mm, origin_y_mm = plan.page.tile_origin_mm(tile.width_mm, tile.height_mm)
        x, y = mm_to_pt(origin_x_mm), mm_to_pt(origin_y_mm)
        pdf.drawImage(ImageReader(str(path)), x, y, width=width, height=height)

        frame = cut_frame(
            x,
            y,
            width,
            height,
            plan.page.room_mm(tile.width_mm, tile.height_mm),
            plan.glue_flap_mm,
            _glue_edges(tile, plan),
        )
        segments = flap_rule_segments(frame)
        _draw_flap_rule(pdf, segments)
        if _draw_crop_marks(pdf, frame) or segments:
            marks_drawn = True
        # The strip was reserved before the grid was chosen, so there is always room.
        if plan.page.caption_strip_mm > 0:
            _draw_label(
                pdf,
                page_width,
                page_height,
                plan.page.margins,
                _tile_caption(plan, tile, stem),
            )
        pdf.showPage()

    pdf.save()

    if not marks_drawn:
        return [
            "no room for cut marks — the tile fills the sheet; "
            "increase --margin or use a finer grid"
        ]
    return []
