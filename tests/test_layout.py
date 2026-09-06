"""Tests for the pure geometry — no image files needed."""

from __future__ import annotations

from itertools import pairwise

import pytest

from imagegrid.layout import (
    FIT_TOLERANCE_MM,
    LayoutError,
    Margins,
    Plan,
    auto_grid,
    build_tiles,
    page_for,
    plan_layout,
    split_boundaries,
)
from imagegrid.units import (
    Orientation,
    UnitError,
    parse_grid,
    parse_length,
    parse_paper,
    parse_size,
    px_to_mm,
)

A4 = (210.0, 297.0)
# The real photograph this tool was written for: 1000 x 800 mm at 100 dpi.
SRC_W, SRC_H, DPI = 3937, 3150, 100.0


def _plan(
    *,
    width_px: int = SRC_W,
    height_px: int = SRC_H,
    dpi: float = DPI,
    paper_mm: tuple[float, float] = A4,
    margins: Margins | None = None,
    orientation: Orientation = Orientation.auto,
    grid: tuple[int, int] | None = None,
) -> Plan:
    return plan_layout(
        width_px=width_px, height_px=height_px, dpi=dpi,
        dpi_source="test", paper_mm=paper_mm,
        margins=margins if margins is not None else Margins.uniform(10.0),
        orientation=orientation, grid=grid,
    )


class TestSplitBoundaries:
    @pytest.mark.parametrize(
        "total,parts",
        [(3937, 5), (3937, 4), (3150, 4), (3150, 5), (100, 3), (7, 7)],
    )
    def test_partitions_exactly(self, total, parts):
        xs = split_boundaries(total, parts)
        assert len(xs) == parts + 1
        assert xs[0] == 0 and xs[-1] == total
        widths = [b - a for a, b in pairwise(xs)]
        # Naive floor division would give 5 * 787 = 3935 and drop 2 px off the right edge.
        assert sum(widths) == total, "every pixel must land in exactly one tile"
        assert max(widths) - min(widths) <= 1
        assert all(w > 0 for w in widths)

    def test_rejects_impossible_splits(self):
        with pytest.raises(LayoutError):
            split_boundaries(100, 0)
        with pytest.raises(LayoutError):
            split_boundaries(3, 4)


class TestBuildTiles:
    def test_covers_the_image_without_gaps_or_overlap(self):
        tiles = build_tiles(SRC_W, SRC_H, 4, 5, DPI)
        assert len(tiles) == 20
        assert sum(t.width_px * t.height_px for t in tiles) == SRC_W * SRC_H
        for t in tiles:
            if t.col > 0:
                left = next(u for u in tiles if u.row == t.row and u.col == t.col - 1)
                assert left.x1 == t.x0
            if t.row > 0:
                above = next(u for u in tiles if u.col == t.col and u.row == t.row - 1)
                assert above.y1 == t.y0

    def test_labels_are_one_based_and_sorted(self):
        tiles = build_tiles(SRC_W, SRC_H, 4, 5, DPI)
        assert tiles[0].label == "r01c01"
        assert tiles[-1].label == "r05c04"

    def test_physical_sizes_sum_to_the_whole(self):
        tiles = build_tiles(SRC_W, SRC_H, 4, 5, DPI)
        row = [t for t in tiles if t.row == 0]
        assert sum(t.width_mm for t in row) == pytest.approx(px_to_mm(SRC_W, DPI))


class TestAutoGrid:
    def test_a4_portrait_10mm(self):
        assert auto_grid(1000.0, 800.1, 190.0, 277.0) == (6, 3)

    def test_a4_landscape_10mm(self):
        assert auto_grid(1000.0, 800.1, 277.0, 190.0) == (4, 5)

    def test_exact_multiple_does_not_round_up(self):
        assert auto_grid(400.0, 200.0, 200.0, 100.0) == (2, 2)

    def test_rejects_zero_printable_area(self):
        with pytest.raises(LayoutError):
            auto_grid(100.0, 100.0, 0.0, 50.0)


class TestFitAtTenMillimetreMargins:
    """At the default 10 mm margin, only one reading of "4 by 5" actually fits A4."""

    def test_four_cols_five_rows_fits_landscape(self):
        plan = _plan(grid=(4, 5))
        assert plan.fits
        assert plan.page.orientation is Orientation.landscape
        assert plan.tile_width_mm == pytest.approx(250.0, abs=0.05)
        assert plan.tile_height_mm == pytest.approx(160.0, abs=0.05)
        assert plan.sheet_count == 20

    def test_five_cols_four_rows_fits_neither_orientation(self):
        plan = _plan(grid=(5, 4))
        assert not plan.fits
        assert max(plan.overflow_mm) == pytest.approx(10.0, abs=0.05)

    @pytest.mark.parametrize("orientation", [Orientation.portrait, Orientation.landscape])
    def test_five_by_four_overflows_when_orientation_is_forced(self, orientation):
        assert not _plan(grid=(5, 4), orientation=orientation).fits

    def test_auto_prefers_the_fewest_sheets(self):
        plan = _plan()
        assert (plan.cols, plan.rows) == (6, 3)
        assert plan.page.orientation is Orientation.portrait
        assert plan.sheet_count == 18
        assert plan.fits

    def test_forcing_landscape_auto_gives_twenty(self):
        plan = _plan(orientation=Orientation.landscape)
        assert (plan.cols, plan.rows) == (4, 5)
        assert plan.sheet_count == 20

    def test_explicit_grid_reports_the_auto_alternative(self):
        assert "18 tiles" in (_plan(grid=(4, 5)).auto_fit_note or "")

    def test_no_alternative_noted_when_the_grid_is_already_optimal(self):
        assert _plan(grid=(6, 3)).auto_fit_note is None


class TestPlanArithmetic:
    def test_physical_size_matches_the_filename(self):
        plan = _plan(grid=(4, 5))
        assert plan.width_mm == pytest.approx(1000.0, abs=0.1)
        assert plan.height_mm == pytest.approx(800.0, abs=0.2)

    def test_spare_space_on_a4_landscape(self):
        plan = _plan(grid=(4, 5))
        sx, sy = plan.spare_mm
        assert sx == pytest.approx(27.0, abs=0.1)
        assert sy == pytest.approx(30.0, abs=0.1)

    def test_grid_finer_than_the_pixels_is_rejected(self):
        with pytest.raises(LayoutError):
            _plan(width_px=3, height_px=3, grid=(10, 1))

    def test_a_page_cannot_be_built_from_an_unresolved_orientation(self):
        """``auto`` is a request, not a page shape; plan_layout resolves it first."""
        with pytest.raises(LayoutError, match="concrete"):
            page_for(A4, Orientation.auto, Margins.uniform(10.0))

    def test_margin_swallowing_the_page_is_rejected(self):
        with pytest.raises(LayoutError):
            _plan(margins=Margins.uniform(120.0))



class TestUnits:
    @pytest.mark.parametrize("text,expected", [
        ("10mm", 10.0), ("10", 10.0), ("1cm", 10.0), ("1in", 25.4),
        ("0.5in", 12.7), ('1"', 25.4), ("72pt", 25.4), (" 10 mm ", 10.0), ("10MM", 10.0),
    ])
    def test_parse_length(self, text, expected):
        assert parse_length(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["", "mm", "ten", "10km", "-5mm", "10x20"])
    def test_parse_length_rejects_junk(self, text):
        with pytest.raises(UnitError):
            parse_length(text)

    def test_parse_size(self):
        assert parse_size("100x80cm") == pytest.approx((1000.0, 800.0))
        assert parse_size("210x297mm") == pytest.approx((210.0, 297.0))
        assert parse_size("210×297") == pytest.approx((210.0, 297.0))

    def test_parse_paper(self):
        assert parse_paper("a4") == (210.0, 297.0)
        assert parse_paper("A4") == (210.0, 297.0)
        assert parse_paper("210x297mm") == (210.0, 297.0)
        with pytest.raises(UnitError):
            parse_paper("a9")

    def test_parse_paper_rejects_a_zero_dimension(self):
        with pytest.raises(UnitError, match="positive"):
            parse_paper("0x297mm")

    def test_parse_grid_is_columns_first(self):
        assert parse_grid("4x5") == (4, 5)
        assert parse_grid("4×5") == (4, 5)
        with pytest.raises(UnitError):
            parse_grid("0x5")
        with pytest.raises(UnitError):
            parse_grid("4-5")


class TestFitTolerance:
    """A tile may exceed the printable area by a hair without being refused.

    Before FIT_TOLERANCE_MM existed, this exact image (the user's 130x95 photo plus one
    pixel of height) was rejected with the self-contradicting message
    "does not fit - over by 0.0 mm tall".
    """

    def _plan(self, height_px: int):
        return plan_layout(
            width_px=6142, height_px=height_px, dpi=120.0, dpi_source="test",
            paper_mm=A4, margins=Margins.uniform(10.0),
            orientation=Orientation.landscape, grid=(5, 5),
        )

    def test_a_hundredth_of_a_millimetre_over_still_fits(self):
        plan = self._plan(4489)
        assert 0 < max(plan.overflow_mm) < FIT_TOLERANCE_MM
        assert plan.fits, "a 0.034 mm overflow is far below what any printer can resolve"

    def test_an_exact_fit_still_fits(self):
        assert self._plan(4488).fits

    @pytest.mark.parametrize(
        "width_px,tile_mm,expected",
        [(19005, 190.05, True), (19020, 190.20, False)],
        ids=["inside the tolerance", "outside it"],
    )
    def test_the_tolerance_boundary(self, width_px, tile_mm, expected):
        """At 2540 dpi one millimetre is exactly 100 px, so the tile lands where intended.

        A4 portrait at a 10 mm margin is 190 mm of printable width, and FIT_TOLERANCE_MM
        forgives a tenth of a millimetre past it.
        """
        plan = _plan(width_px=width_px, height_px=20000, dpi=2540.0, grid=(1, 1))
        assert plan.tile_width_mm == pytest.approx(tile_mm, abs=0.001)
        assert plan.fits is expected


class TestAutoFitNoteAgreesWithAutoFit:
    """The note must never name a grid that auto-fit would not actually choose."""

    def test_note_matches_a_real_auto_run(self):
        forced = _plan(grid=(4, 5))
        auto = _plan(grid=None)
        assert forced.auto_fit_note is not None
        assert f"{auto.cols}x{auto.rows}" in forced.auto_fit_note
        assert f"= {auto.sheet_count} tiles" in forced.auto_fit_note
        assert auto.page.orientation.value in forced.auto_fit_note

    def test_no_note_when_the_forced_grid_is_the_auto_one(self):
        auto = _plan(grid=None)
        assert _plan(grid=(auto.cols, auto.rows)).auto_fit_note is None
