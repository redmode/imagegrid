"""Everything drawn on a tile page must survive a real printer.

A printer cannot lay ink in the outer few millimetres of a sheet, which is exactly what
``--margin`` declares. A PDF viewer happily shows content out there, so these checks work
in millimetres from the paper edge rather than by looking at a rendering: an earlier
version put the tile caption 2.5 mm from the edge, which looked perfect on screen and was
simply absent on paper.
"""

from __future__ import annotations

import pytest

from imagegrid.layout import CAPTION_STRIP_MM, LayoutError, Margins, Page, page_for, plan_layout
from imagegrid.pdf import (
    LABEL_BASELINE_BELOW_MARGIN_MM,
    MARK_MIN_SPACE_MM,
    _mark_geometry,
)
from imagegrid.units import MM_PER_INCH, PT_PER_INCH, Orientation

MARGIN_MM = 10.0
A4 = (210.0, 297.0)


def pt_to_mm(pt: float) -> float:
    return pt / PT_PER_INCH * MM_PER_INCH


def mark_mm(band_mm: float) -> tuple[float, float]:
    """Mark offset and length in mm, failing loudly if no mark would be drawn."""
    geometry = _mark_geometry(band_mm)
    assert geometry is not None, f"expected a cut mark for a {band_mm:.2f} mm band"
    offset, length = geometry
    return pt_to_mm(offset), pt_to_mm(length)


class TestCaptionStripSizing:
    def test_the_strip_is_tall_enough_for_the_line_it_holds(self):
        """The baseline drops into the strip and the glyphs must not fall out of it."""
        assert LABEL_BASELINE_BELOW_MARGIN_MM < CAPTION_STRIP_MM
        assert LABEL_BASELINE_BELOW_MARGIN_MM > 2.5, "7pt cap height, measured upward"


class TestMarkGeometry:
    """``_mark_geometry`` takes the printable band beside a tile and sizes one arm.

    Where that band comes from is the page model's business; that the arm always stays
    inside it is this function's. Whether the finished PDF honours it is proved against
    real output in test_pdf_geometry.
    """

    @pytest.mark.parametrize("band_mm", [1.5, 2.0, 3.5, 5.15, 11.7, 40.0])
    def test_an_arm_never_reaches_past_its_band(self, band_mm):
        offset_mm, length_mm = mark_mm(band_mm)
        assert offset_mm + length_mm <= band_mm + 1e-9

    @pytest.mark.parametrize("band_mm", [1.5, 5.15, 11.7, 40.0])
    def test_an_arm_starts_close_to_the_corner_it_marks(self, band_mm):
        offset_mm, _ = mark_mm(band_mm)
        assert 0 < offset_mm <= 2.0

    def test_a_generous_band_gets_the_full_length(self):
        offset_mm, length_mm = mark_mm(11.7)
        assert offset_mm == pytest.approx(2.0, abs=0.01)
        assert length_mm == pytest.approx(5.0, abs=0.01)

    def test_a_narrow_band_shortens_the_arm_rather_than_overflowing(self):
        _, length_mm = mark_mm(5.15)
        assert length_mm == pytest.approx(5.15 - 2.0, abs=0.01)

    @pytest.mark.parametrize("band_mm", [0.0, 0.5, 1.4])
    def test_no_arm_at_all_when_the_band_is_too_thin(self, band_mm):
        assert _mark_geometry(band_mm) is None

    def test_the_constants_cannot_produce_a_useless_arm(self):
        """The minimum band must always buy an arm long enough to see and cut against.

        This is what lets the function return a mark or nothing, with no third case: a
        band at the very threshold still yields a full millimetre of line.
        """
        shortest = min(
            mark_mm(MARK_MIN_SPACE_MM + step / 1000)[1] for step in range(0, 60_000)
        )
        assert shortest >= 1.0 - 1e-9, (
            f"shortest arm is {shortest:.3f} mm — add a lower bound back"
        )


class TestPerSideMargins:
    """Asymmetric margins move the printable area off-centre, and the tile must follow it."""

    PAGE_W, PAGE_H = A4

    def _page(self, **sides) -> Page:
        margins = Margins(**{**{"top": 10.0, "right": 10.0, "bottom": 10.0, "left": 10.0}, **sides})
        return page_for((self.PAGE_W, self.PAGE_H), Orientation.portrait, margins)

    def test_printable_area_subtracts_each_side_once(self):
        page = self._page(left=8.0, right=18.0, top=5.0, bottom=25.0)
        assert page.printable_width_mm == pytest.approx(self.PAGE_W - 8.0 - 18.0)
        assert page.printable_height_mm == pytest.approx(self.PAGE_H - 5.0 - 25.0)

    def test_tile_is_centred_in_the_printable_area_not_the_page(self):
        """The regression this design exists to prevent: clipped image, not just a caption."""
        page = self._page(top=5.0, bottom=20.0)
        tile_h = page.printable_height_mm  # the largest tile that "fits"
        _, y = page.tile_origin_mm(100.0, tile_h)
        assert y == pytest.approx(20.0), "must start at the bottom margin"
        assert y + tile_h == pytest.approx(self.PAGE_H - 5.0), "and end at the top margin"
        page_centred = (self.PAGE_H - tile_h) / 2
        assert page_centred < 20.0, "page-centring would have pushed image into the dead zone"

    def test_tile_never_leaves_the_printable_area(self):
        page = self._page(left=8.0, right=18.0, top=5.0, bottom=25.0)
        tile_w, tile_h = 150.0, 200.0
        x, y = page.tile_origin_mm(tile_w, tile_h)
        assert x >= 8.0 and x + tile_w <= self.PAGE_W - 18.0
        assert y >= 25.0 and y + tile_h <= self.PAGE_H - 5.0

    def test_bands_stay_symmetric_so_marks_need_only_one_value_per_axis(self):
        page = self._page(left=8.0, right=18.0, top=5.0, bottom=25.0)
        tile_w, tile_h = 150.0, 200.0
        x, y = page.tile_origin_mm(tile_w, tile_h)
        band_x, band_y = page.band_mm(tile_w, tile_h)
        assert x - 8.0 == pytest.approx(band_x)
        assert (self.PAGE_W - 18.0) - (x + tile_w) == pytest.approx(band_x)
        assert y - 25.0 == pytest.approx(band_y)
        assert (self.PAGE_H - 5.0) - (y + tile_h) == pytest.approx(band_y)

    def test_uniform_margins_still_centre_on_the_page(self):
        """The pre-existing behaviour must be untouched when all four sides match."""
        page = self._page()
        x, y = page.tile_origin_mm(150.0, 200.0)
        assert x == pytest.approx((self.PAGE_W - 150.0) / 2)
        assert y == pytest.approx((self.PAGE_H - 200.0) / 2)

    def test_margins_do_not_rotate_with_orientation(self):
        margins = Margins(top=5.0, right=10.0, bottom=25.0, left=8.0)
        landscape = page_for((self.PAGE_W, self.PAGE_H), Orientation.landscape, margins)
        assert (landscape.width_mm, landscape.height_mm) == (self.PAGE_H, self.PAGE_W)
        assert landscape.margins.bottom == 25.0, "bottom stays the bottom of the printed page"

    @pytest.mark.parametrize("side", ["top", "right", "bottom", "left"])
    def test_rejects_a_negative_side(self, side):
        with pytest.raises(LayoutError, match=side):
            Margins(**{**{"top": 1.0, "right": 1.0, "bottom": 1.0, "left": 1.0}, side: -1.0})

    def test_rejects_margins_that_swallow_an_axis(self):
        with pytest.raises(LayoutError, match="left and right"):
            self._page(left=150.0, right=150.0)

    def test_uniform_helper_and_formatting(self):
        assert Margins.uniform(10.0).is_uniform
        assert str(Margins.uniform(10.0)) == "margin 10 mm"
        assert str(Margins(5.0, 10.0, 25.0, 8.0)) == "margins t5 r10 b25 l8 mm"


class TestReservedGlueFlap:
    """The flap is subtracted before the grid is chosen, like the caption strip.

    Taking it from whatever white happened to be left over did not work: on a real 72x50
    poster the band came to 4.979 mm against a 5 mm flap, and on a 37x50 one to 0.05 mm, so
    the cut line could not be printed and that edge fell back to marks so faint they read
    as a missing flap. Reserving makes the room unconditional.
    """

    def _plan(self, width_px: int, height_px: int, dpi: float, flap: float):
        return plan_layout(
            width_px=width_px, height_px=height_px, dpi=dpi, dpi_source="t",
            paper_mm=A4, margins=Margins.uniform(10.0),
            caption_strip_mm=CAPTION_STRIP_MM, glue_flap_mm=flap,
            orientation=Orientation.auto,
        )

    @pytest.mark.parametrize(
        "width_px,height_px,dpi",
        [(4819, 3346, 170.0), (2362, 1747, 120.0), (3937, 3150, 100.0)],
        ids=["72x50 poster", "37x50 poster", "100x80 poster"],
    )
    def test_every_flapped_edge_has_room_for_its_cut_line(self, width_px, height_px, dpi):
        """All three of these had at least one edge that could not print its line."""
        plan = self._plan(width_px, height_px, dpi, 5.0)
        room = plan.page.room_mm(plan.tile_width_mm, plan.tile_height_mm)
        for edge in plan.flapped_edges:
            assert room[edge] >= 5.0 - 1e-9, f"{edge} edge has only {room[edge]:.3f} mm"

    def test_the_room_is_the_band_plus_the_reservation(self):
        plan = self._plan(4819, 3346, 170.0, 5.0)
        band_x, band_y = plan.page.band_mm(plan.tile_width_mm, plan.tile_height_mm)
        room = plan.page.room_mm(plan.tile_width_mm, plan.tile_height_mm)
        assert room["left"] == pytest.approx(band_x)
        assert room["right"] == pytest.approx(band_x + 5.0)
        assert room["top"] == pytest.approx(band_y)
        assert room["bottom"] == pytest.approx(band_y + 5.0)

    def test_a_single_column_reserves_nothing_on_the_right(self):
        """Nothing sits to the right of a one-column grid, so the width is not spent."""
        plan = self._plan(600, 4000, 100.0, 5.0)
        assert plan.cols == 1
        assert plan.page.flap_right_mm == 0.0
        assert plan.page.content_width_mm == plan.page.printable_width_mm

    def test_a_single_row_reserves_nothing_below(self):
        plan = self._plan(4000, 600, 100.0, 5.0)
        assert plan.rows == 1
        assert plan.page.flap_bottom_mm == 0.0

    def test_reserving_can_cost_a_sheet_and_that_is_the_trade(self):
        """The 37x50 poster sits 0.2 mm inside a two-row fit, so the flap tips it to three."""
        assert self._plan(2362, 1747, 120.0, 0.0).sheet_count == 4
        assert self._plan(2362, 1747, 120.0, 5.0).sheet_count == 6

    def test_most_layouts_pay_nothing(self):
        for width_px, height_px, dpi in ((3937, 3150, 100.0), (4819, 3346, 170.0)):
            without = self._plan(width_px, height_px, dpi, 0.0).sheet_count
            with_flap = self._plan(width_px, height_px, dpi, 5.0).sheet_count
            assert with_flap == without

    def test_the_tile_sits_above_the_reserved_strip(self):
        plan = self._plan(4819, 3346, 170.0, 5.0)
        _, y = plan.page.tile_origin_mm(plan.tile_width_mm, plan.tile_height_mm)
        assert y >= plan.page.margins.bottom + 5.0 - 1e-9

    def test_a_flap_that_swallows_the_page_is_refused(self):
        with pytest.raises(LayoutError, match="glue flap"):
            page_for(A4, Orientation.portrait, Margins.uniform(10.0), CAPTION_STRIP_MM, 200.0)

    def test_a_negative_flap_is_refused(self):
        with pytest.raises(LayoutError, match="glue flap cannot be negative"):
            page_for(A4, Orientation.portrait, Margins.uniform(10.0), 0.0, -1.0)


class TestReservedCaptionStrip:
    """The caption strip is subtracted before the grid is chosen, so it cannot be eaten.

    Auto-fit maximises the tile to minimise sheets. Any space not reserved up front gets
    consumed, which is why raising the bottom margin used to *remove* the caption: it
    shrank the printable height without changing the row count, so the leftover crumb the
    caption depended on got smaller.
    """

    def _plan(self, strip: float, margins: Margins | None = None):
        return plan_layout(
            width_px=3937, height_px=3150, dpi=100.0, dpi_source="t",
            paper_mm=A4, margins=margins or Margins.uniform(10.0),
            caption_strip_mm=strip, orientation=Orientation.auto,
        )

    def test_content_area_is_the_printable_area_less_the_strip(self):
        page = self._plan(CAPTION_STRIP_MM).page
        assert page.content_height_mm == pytest.approx(page.printable_height_mm - CAPTION_STRIP_MM)
        assert page.content_width_mm == pytest.approx(page.printable_width_mm), "bottom only"

    def test_the_tile_clears_the_strip(self):
        """The strip is at the top, so the tile must stop short of it."""
        plan = self._plan(CAPTION_STRIP_MM)
        _, y = plan.page.tile_origin_mm(plan.tile_width_mm, plan.tile_height_mm)
        strip_floor = plan.page.height_mm - plan.page.margins.top - CAPTION_STRIP_MM
        assert y + plan.tile_height_mm <= strip_floor + 1e-9

    def test_the_caption_baseline_lands_inside_the_strip(self):
        page = self._plan(CAPTION_STRIP_MM).page
        margin_line = page.height_mm - page.margins.top
        baseline = margin_line - LABEL_BASELINE_BELOW_MARGIN_MM
        assert margin_line - CAPTION_STRIP_MM < baseline < margin_line

    @pytest.mark.parametrize("top", [10.0, 15.0, 20.0, 25.0])
    def test_room_exists_whatever_the_top_margin(self, top):
        """Reserving is what makes the caption independent of the margin around it."""
        margins = Margins(top=top, right=10.0, bottom=10.0, left=10.0)
        plan = self._plan(CAPTION_STRIP_MM, margins)
        assert plan.page.caption_strip_mm == CAPTION_STRIP_MM
        _, y = plan.page.tile_origin_mm(plan.tile_width_mm, plan.tile_height_mm)
        strip_floor = plan.page.height_mm - top - CAPTION_STRIP_MM
        assert y + plan.tile_height_mm <= strip_floor + 1e-9

    def test_reserving_is_free_on_the_sample_image(self):
        assert self._plan(CAPTION_STRIP_MM).sheet_count == self._plan(0.0).sheet_count == 18

    def test_no_strip_restores_the_full_printable_area(self):
        page = self._plan(0.0).page
        assert page.content_height_mm == pytest.approx(page.printable_height_mm)

    def test_fit_check_measures_against_the_content_area(self):
        """A tile that would fit the printable area but not the strip must be refused."""
        page = page_for(A4, Orientation.portrait, Margins.uniform(10.0), CAPTION_STRIP_MM)
        _, spare_y = page.spare_for(100.0, page.printable_height_mm)
        assert spare_y == pytest.approx(-CAPTION_STRIP_MM)

    def test_the_strip_counts_against_the_vertical_budget(self):
        """292 mm of margin fits a 297 mm page; the same plus a 5 mm strip does not."""
        tall = Margins(top=142.0, right=10.0, bottom=150.0, left=10.0)
        page_for(A4, Orientation.portrait, tall)  # fine without the strip
        with pytest.raises(LayoutError, match="top and bottom"):
            page_for(A4, Orientation.portrait, tall, CAPTION_STRIP_MM)

    def test_rejects_a_negative_strip(self):
        with pytest.raises(LayoutError, match="caption strip"):
            page_for(A4, Orientation.portrait, Margins.uniform(10.0), -1.0)
