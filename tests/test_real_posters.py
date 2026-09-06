"""Every real poster is planned, and the invariants that failed on it in the past hold.

Each fixture earned its place by breaking something. The dimensions and resolutions here
are asserted rather than read, so re-encoding or swapping a file fails loudly instead of
quietly turning the case it represents into a different one.

Only headers are read: `Image.open` is lazy, and none of these tests needs a pixel.
"""

from __future__ import annotations

import pytest
from PIL import Image

from conftest import POSTERS
from imagegrid.layout import CAPTION_STRIP_MM, GLUE_FLAP_MM, Margins, plan_layout
from imagegrid.resolution import resolve_dpi
from imagegrid.units import PAPERS, Orientation

MARGINS = Margins.uniform(10.0)


def plan_for(poster, *, flap: float = GLUE_FLAP_MM, grid=None):
    return plan_layout(
        width_px=poster.width_px, height_px=poster.height_px, dpi=poster.dpi,
        dpi_source="fixture", paper_mm=PAPERS["a4"], margins=MARGINS,
        caption_strip_mm=CAPTION_STRIP_MM, glue_flap_mm=flap,
        orientation=Orientation.auto, grid=grid,
    )


class TestTheFixturesThemselves:
    """Guard the files: if these fail, the image changed, not the code."""

    def test_the_header_says_what_the_registry_claims(self, poster):
        image = Image.open(poster.path)
        assert (image.width, image.height) == (poster.width_px, poster.height_px)
        assert resolve_dpi(image).dpi == poster.dpi

    def test_each_one_is_kept_for_a_stated_reason(self, poster):
        assert poster.kept_for, "a fixture nobody can justify is a fixture nobody maintains"

    def test_the_registry_has_no_duplicates(self):
        shapes = {(p.width_px, p.height_px, p.dpi) for p in POSTERS}
        assert len(shapes) == len(POSTERS), "two fixtures testing the same geometry"


class TestEveryPosterPlans:
    def test_it_fits_a4(self, poster):
        assert plan_for(poster).fits

    def test_every_flapped_edge_has_room_for_its_cut_line(self, poster):
        """The reservation exists so this is unconditional; three of these once failed it."""
        plan = plan_for(poster)
        room = plan.page.room_mm(plan.tile_width_mm, plan.tile_height_mm)
        for edge in plan.flapped_edges:
            assert room[edge] >= GLUE_FLAP_MM - 1e-9, (
                f"{edge} edge has {room[edge]:.3f} mm, short of a {GLUE_FLAP_MM:g} mm flap"
            )

    def test_the_tiles_reassemble_to_the_whole_image(self, poster):
        plan = plan_for(poster)
        assert sum(t.width_px * t.height_px for t in plan.tiles) == (
            poster.width_px * poster.height_px
        )

    def test_the_finished_size_is_the_one_in_the_filename(self, poster):
        """poster_72x50cm.jpg really is 720 x 500 mm, give or take rounding."""
        plan = plan_for(poster)
        stem = poster.filename.removeprefix("poster_").removesuffix("cm.jpg")
        wanted = [float(n) * 10 for n in stem.split("x")]
        got = sorted((plan.width_mm, plan.height_mm))
        assert got == pytest.approx(sorted(wanted), abs=1.0)

    def test_the_caption_strip_and_flap_both_come_out_of_the_page(self, poster):
        page = plan_for(poster).page
        assert page.content_height_mm == pytest.approx(
            page.printable_height_mm - CAPTION_STRIP_MM - page.flap_bottom_mm
        )
        assert page.content_width_mm == pytest.approx(
            page.printable_width_mm - page.flap_right_mm
        )


class TestWhatTheFlapCosts:
    """The trade for a guaranteed cut line, measured on real posters rather than guessed."""

    EXPECTED = {
        "poster_100x80cm.jpg": (18, 18),
        "poster_130x95cm.jpg": (28, 30),
        "poster_105x150cm.jpg": (36, 36),
        "poster_72x50cm.jpg": (8, 8),
        "poster_37x50cm.jpg": (4, 6),
    }

    def test_the_sheet_count_matches_what_was_measured(self, poster):
        plain, reserved = self.EXPECTED[poster.filename]
        assert plan_for(poster, flap=0.0).sheet_count == plain
        assert plan_for(poster).sheet_count == reserved

    def test_reserving_never_saves_sheets(self, poster):
        assert plan_for(poster).sheet_count >= plan_for(poster, flap=0.0).sheet_count

    def test_most_posters_pay_nothing(self):
        free = sum(1 for plain, reserved in self.EXPECTED.values() if plain == reserved)
        assert free >= 3, "if reserving started costing everywhere, reconsider the default"

    def test_the_whole_set_costs_about_four_percent(self):
        plain = sum(p for p, _ in self.EXPECTED.values())
        reserved = sum(r for _, r in self.EXPECTED.values())
        assert (reserved - plain) / plain < 0.06
