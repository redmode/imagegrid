"""What a finished PDF actually puts on paper, read back from its content stream.

These are the tests that would have caught the two worst regressions this tool has had.
Both were found by hand, and both passed every test in the suite at the time:

* the tile caption drawn 2.5 mm from the paper edge, inside the printer's dead zone, so it
  looked right in a viewer and was simply absent on paper;
* tiles centred on the page rather than the printable area, which with asymmetric margins
  pushes real image into the dead zone and clips the poster.

Nothing here recomputes a position from the constants the code uses — that is what let
both bugs through. Every assertion measures the ink.
"""

from __future__ import annotations

import pytest
from PIL import Image

from imagegrid.layout import CAPTION_STRIP_MM, GLUE_FLAP_MM, Margins, plan_layout
from imagegrid.pdf import build_pdf
from imagegrid.tiles import guide_image, write_tiles
from imagegrid.units import PAPERS, Orientation
from pdf_probe import media_boxes_mm, read_pages

A4 = PAPERS["a4"]
UNIFORM = Margins.uniform(10.0)
# Deliberately lopsided, and deeper at the bottom the way a real inkjet is.
LOPSIDED = Margins(top=5.0, right=18.0, bottom=25.0, left=8.0)


def build(
    tmp_path,
    *,
    margins: Margins = UNIFORM,
    caption: bool = True,
    orientation: Orientation = Orientation.portrait,
    grid: tuple[int, int] | None = (2, 2),
    stem: str = "s",
    px: tuple[int, int] = (600, 400),
    glue_flap_mm: float = 0.0,
):
    """A real PDF with no guide page, so every page is a tile page."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", px, "slategrey")
    plan = plan_layout(
        width_px=px[0],
        height_px=px[1],
        dpi=50.0,
        dpi_source="test",
        paper_mm=A4,
        margins=margins,
        caption_strip_mm=CAPTION_STRIP_MM if caption else 0.0,
        glue_flap_mm=glue_flap_mm,
        orientation=orientation,
        grid=grid,
    )
    assert plan.fits, "the fixture layout must be printable"
    written = write_tiles(image, plan, tmp_path, stem, 90)
    path = tmp_path / f"{stem}_print.pdf"
    build_pdf(plan, written, path, stem)
    return plan, read_pages(path), path


def printable_bounds(plan) -> tuple[float, float, float, float]:
    """(left, bottom, right, top) of what the printer can reach, in mm."""
    page, m = plan.page, plan.page.margins
    return (m.left, m.bottom, page.width_mm - m.right, page.height_mm - m.top)


class TestNothingLandsInTheDeadZone:
    """The single check that kills both historical regressions at once."""

    @pytest.mark.parametrize("margins", [UNIFORM, LOPSIDED], ids=["uniform", "lopsided"])
    def test_all_ink_is_inside_the_margins(self, tmp_path, margins):
        plan, pages, _ = build(tmp_path, margins=margins)
        left, bottom, right, top = printable_bounds(plan)
        for number, page in enumerate(pages, start=1):
            ink_l, ink_b, ink_r, ink_t = page.ink_extent_mm()
            overruns = {
                "left": left - ink_l,
                "bottom": bottom - ink_b,
                "right": ink_r - right,
                "top": ink_t - top,
            }
            worst = max(overruns, key=lambda side: overruns[side])
            assert overruns[worst] <= 1e-6, (
                f"page {number} draws {overruns[worst]:.2f} mm past the {worst} margin, "
                "where the printer cannot reach"
            )

    def test_the_caption_glyphs_fit_between_the_side_margins(self, tmp_path):
        """A long filename must not push the centred caption off the printable area."""
        plan, pages, _ = build(tmp_path, stem="a" * 60)
        left, _, right, _ = printable_bounds(plan)
        for page in pages:
            caption = page.captions[0]
            assert caption.width_mm > 100.0, "the fixture stem must actually be long"
            assert caption.left_mm >= left - 1e-6
            assert caption.right_mm <= right + 1e-6


class TestCaptionPlacement:
    """The 0.4.1 regression: a caption in the dead zone prints as nothing at all."""

    def test_every_tile_page_carries_exactly_one_caption(self, tmp_path):
        plan, pages, _ = build(tmp_path)
        assert len(pages) == plan.sheet_count
        assert [len(page.captions) for page in pages] == [1] * plan.sheet_count

    def test_the_baseline_sits_below_the_top_margin(self, tmp_path):
        """The caption is at the top because a glue flap keeps part of the bottom border."""
        plan, pages, _ = build(tmp_path)
        margin_line = plan.page.height_mm - plan.page.margins.top
        for page in pages:
            assert page.captions[0].baseline_mm < margin_line

    def test_the_baseline_sits_inside_the_reserved_strip(self, tmp_path):
        plan, pages, _ = build(tmp_path)
        margin_line = plan.page.height_mm - plan.page.margins.top
        baseline = pages[0].captions[0].baseline_mm
        assert margin_line - CAPTION_STRIP_MM < baseline < margin_line

    def test_the_caption_is_above_the_image(self, tmp_path):
        _, pages, _ = build(tmp_path)
        assert pages[0].captions[0].baseline_mm > pages[0].images[0].top_mm

    @pytest.mark.parametrize("top_mm", [0.0, 10.0, 20.0, 30.0])
    def test_the_baseline_follows_the_top_margin(self, tmp_path, top_mm):
        """Not a fixed distance from the paper edge — that was precisely the bug."""
        margins = Margins(top=top_mm, right=10.0, bottom=10.0, left=10.0)
        plan, pages, _ = build(tmp_path, margins=margins)
        expected = plan.page.height_mm - top_mm - 4.0
        assert pages[0].captions[0].baseline_mm == pytest.approx(expected, abs=0.01)

    def test_the_caption_is_centred_on_the_page(self, tmp_path):
        plan, pages, _ = build(tmp_path)
        assert pages[0].captions[0].centre_mm == pytest.approx(plan.page.width_mm / 2, abs=0.05)

    def test_the_caption_names_its_own_tile(self, tmp_path):
        _, pages, _ = build(tmp_path)
        assert [page.captions[0].text[:6] for page in pages] == [
            "r01c01",
            "r01c02",
            "r02c01",
            "r02c02",
        ]

    def test_no_caption_when_the_strip_is_not_reserved(self, tmp_path):
        _, pages, _ = build(tmp_path, caption=False)
        assert all(page.captions == [] for page in pages)


class TestTilePlacement:
    """The 0.5.0 regression: page-centring clips image once margins are asymmetric."""

    def test_the_tile_is_drawn_at_its_exact_physical_size(self, tmp_path):
        """The whole promise of the tool: 100% scale means these millimetres."""
        plan, pages, _ = build(tmp_path)
        for tile, page in zip(plan.tiles, pages, strict=True):
            drawn = page.images[0]
            assert drawn.width_mm == pytest.approx(tile.width_mm, abs=0.01)
            assert drawn.height_mm == pytest.approx(tile.height_mm, abs=0.01)

    def test_placement_matches_what_the_page_model_says(self, tmp_path):
        plan, pages, _ = build(tmp_path, margins=LOPSIDED)
        for tile, page in zip(plan.tiles, pages, strict=True):
            want_x, want_y = plan.page.tile_origin_mm(tile.width_mm, tile.height_mm)
            assert page.images[0].x_mm == pytest.approx(want_x, abs=0.01)
            assert page.images[0].y_mm == pytest.approx(want_y, abs=0.01)

    def test_lopsided_margins_are_not_page_centred(self, tmp_path):
        """Guards the fix directly: page-centring would put the tile somewhere else."""
        plan, pages, _ = build(tmp_path, margins=LOPSIDED)
        drawn = pages[0].images[0]
        page_centred_y = (plan.page.height_mm - drawn.height_mm) / 2
        assert drawn.y_mm > page_centred_y + 1.0, "must sit above where page-centring puts it"
        assert drawn.y_mm >= LOPSIDED.bottom, "and clear of the bottom dead zone"

    def test_the_bands_around_the_tile_are_equal(self, tmp_path):
        """Centring in the content area is what makes one band value per axis correct."""
        plan, pages, _ = build(tmp_path, margins=LOPSIDED)
        left, _, right, top = printable_bounds(plan)
        drawn = pages[0].images[0]
        assert drawn.x_mm - left == pytest.approx(right - drawn.right_mm, abs=0.01)
        assert (top - CAPTION_STRIP_MM) - drawn.top_mm == pytest.approx(
            drawn.y_mm - LOPSIDED.bottom, abs=0.01
        )


class TestCropMarks:
    def test_eight_marks_per_page_when_both_bands_have_room(self, tmp_path):
        _, pages, _ = build(tmp_path)
        assert all(len(page.lines) == 8 for page in pages), "four corners, two arms each"

    def test_marks_sit_outside_the_image_they_mark(self, tmp_path):
        plan, pages, _ = build(tmp_path)
        drawn = pages[0].images[0]
        for line in pages[0].lines:
            outside_x = max(line.xs_mm) <= drawn.x_mm or min(line.xs_mm) >= drawn.right_mm
            outside_y = max(line.ys_mm) <= drawn.y_mm or min(line.ys_mm) >= drawn.top_mm
            assert outside_x or outside_y, "a mark must never be drawn over the image"

    def test_marks_are_dropped_on_the_axis_with_no_room(self, tmp_path):
        """A tile filling the content height leaves nowhere for the vertical arms.

        535 px at 50 dpi is 271.8 mm, against a content height of 272 mm.
        """
        plan, pages, _ = build(tmp_path, grid=(2, 1), px=(600, 535))
        band_x, band_y = plan.page.band_mm(plan.tile_width_mm, plan.tile_height_mm)
        assert band_y < 1.5 <= band_x, "fixture must be tight on one axis only"
        assert all(len(page.lines) == 4 for page in pages), "horizontal arms only"


class TestGlueFlapMarks:
    """Where the flap is cut, and how the sheet says so.

    Two treatments, because the flap's cut line lies outside the image and the printer
    cannot always reach that far. Where it can, the line itself is drawn full length and
    there is nothing to measure; where it cannot, the corner arms turn dashed and the
    caption carries the distance.
    """

    def edge_lines(self, page):
        """Lines grouped by the tile edge they sit on, split into rules and corner arms.

        A crop mark and a flap rule can share a coordinate — the marks are drawn at the
        corners of the piece, which is exactly where the rule runs — so they are told apart
        by length: an arm is at most 5 mm, a rule spans a whole edge.
        """
        drawn = page.images[0]
        near = lambda a, b: abs(a - b) < 0.01  # noqa: E731
        span = lambda ln: max(  # noqa: E731
            abs(ln.x1_mm - ln.x0_mm), abs(ln.y1_mm - ln.y0_mm)
        )
        flap = GLUE_FLAP_MM
        at = {
            "bottom_arm": lambda ln: ln.is_horizontal and near(ln.y0_mm, drawn.y_mm),
            "bottom_rule": lambda ln: ln.is_horizontal and near(ln.y0_mm, drawn.y_mm - flap),
            "right_arm": lambda ln: not ln.is_horizontal and near(ln.x0_mm, drawn.right_mm),
            "right_rule": (
                lambda ln: not ln.is_horizontal and near(ln.x0_mm, drawn.right_mm + flap)
            ),
            "top_arm": lambda ln: ln.is_horizontal and near(ln.y0_mm, drawn.top_mm),
            "left_arm": lambda ln: not ln.is_horizontal and near(ln.x0_mm, drawn.x_mm),
        }
        return {
            name: [
                ln
                for ln in page.lines
                if matches(ln) and ((span(ln) > 20.0) == name.endswith("rule"))
            ]
            for name, matches in at.items()
        }

    def test_the_flap_cut_line_is_drawn_where_it_fits(self, tmp_path):
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        lines = self.edge_lines(pages[0])
        assert len(lines["bottom_rule"]) == 1 and len(lines["right_rule"]) == 1
        assert all(ln.dashed for ln in lines["bottom_rule"] + lines["right_rule"])

    def test_the_rule_runs_the_whole_edge_not_just_the_corners(self, tmp_path):
        """A dash over a 5 mm corner arm is invisible; over a full edge it is obvious."""
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        drawn = pages[0].images[0]
        rule = self.edge_lines(pages[0])["bottom_rule"][0]
        assert abs(rule.x1_mm - rule.x0_mm) >= drawn.width_mm

    def test_the_two_rules_meet_at_the_outer_corner(self, tmp_path):
        """Together they trace the real outline of the piece being cut out."""
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        drawn = pages[0].images[0]
        lines = self.edge_lines(pages[0])
        corner = (drawn.right_mm + GLUE_FLAP_MM, drawn.y_mm - GLUE_FLAP_MM)
        assert max(lines["bottom_rule"][0].xs_mm) == pytest.approx(corner[0], abs=0.01)
        assert min(lines["right_rule"][0].ys_mm) == pytest.approx(corner[1], abs=0.01)

    def test_no_arm_is_left_behind_at_the_image_edge(self, tmp_path):
        """A mark back at the image edge would invite cutting in the wrong place."""
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        lines = self.edge_lines(pages[0])
        assert lines["bottom_arm"] == [] and lines["right_arm"] == []

    def test_corner_marks_move_out_onto_the_piece(self, tmp_path):
        """The marks belong on the rectangle the blade follows, flap included."""
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        drawn = pages[0].images[0]
        arms = [ln for ln in pages[0].lines if not ln.dashed]
        assert len(arms) == 8, "four corners of the piece, two arms each"
        corners = {
            (
                round(min(ln.xs_mm) if ln.is_horizontal else ln.x0_mm, 1),
                round(ln.y0_mm if ln.is_horizontal else min(ln.ys_mm), 1),
            )
            for ln in arms
        }
        assert any(abs(cy - (drawn.y_mm - GLUE_FLAP_MM)) < 6.0 for _, cy in corners), (
            "arms reach down to the flap edge, not just the image edge"
        )

    def test_the_marks_bracket_the_flap_corner(self, tmp_path):
        """The outer corner of the piece gets a real L, which is what a blade lines up on."""
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        drawn = pages[0].images[0]
        corner_x = drawn.right_mm + GLUE_FLAP_MM
        corner_y = drawn.y_mm - GLUE_FLAP_MM
        arms = [ln for ln in pages[0].lines if not ln.dashed]
        assert any(
            ln.is_horizontal and abs(ln.y0_mm - corner_y) < 0.01 and min(ln.xs_mm) > corner_x
            for ln in arms
        ), "a horizontal arm running out past the flap corner"
        assert any(
            not ln.is_horizontal and abs(ln.x0_mm - corner_x) < 0.01 and min(ln.ys_mm) < corner_y
            for ln in arms
        ), "a vertical arm running down past the flap corner"

    def test_cut_edges_keep_their_solid_arms(self, tmp_path):
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        lines = self.edge_lines(pages[0])
        assert len(lines["top_arm"]) == 2 and len(lines["left_arm"]) == 2
        assert not any(ln.dashed for ln in lines["top_arm"] + lines["left_arm"])

    def test_every_corner_arm_is_solid_once_the_rule_is_drawn(self, tmp_path):
        """Solid always means "the blade goes here"; only the rule carries the dashes."""
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        short = [
            ln
            for ln in pages[0].lines
            if max(abs(ln.x1_mm - ln.x0_mm), abs(ln.y1_mm - ln.y0_mm)) <= 20.0
        ]
        assert len(short) == 8
        assert not any(ln.dashed for ln in short)

    def test_the_rule_always_prints_because_its_room_is_reserved(self, tmp_path):
        """A tile flush against the flap used to leave nowhere to draw the line."""
        plan, pages, _ = build(tmp_path, grid=None, px=(600, 1055), glue_flap_mm=GLUE_FLAP_MM)
        room = plan.page.room_mm(plan.tile_width_mm, plan.tile_height_mm)
        assert room["bottom"] >= GLUE_FLAP_MM and room["right"] >= GLUE_FLAP_MM
        lines = self.edge_lines(pages[0])
        assert lines["bottom_rule"] and lines["right_rule"]
        assert lines["bottom_arm"] == [] and lines["right_arm"] == []

    def test_the_last_row_and_column_are_cut_not_glued(self, tmp_path):
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        assert not any(line.dashed for line in pages[-1].lines)
        assert len(pages[-1].lines) == 8, "plain solid marks on all four corners"

    def test_a_last_column_tile_still_glues_its_bottom(self, tmp_path):
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        lines = self.edge_lines(pages[1])  # r01c02
        assert lines["bottom_rule"], "a tile sits below it"
        assert lines["right_rule"] == [], "nothing sits to its right"
        assert len(lines["right_arm"]) == 2

    def test_no_flap_leaves_every_arm_solid(self, tmp_path):
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=0.0)
        assert not any(line.dashed for page in pages for line in page.lines)
        assert all(len(page.lines) == 8 for page in pages)

    def test_the_caption_states_the_flap_and_its_edges(self, tmp_path):
        _, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        assert "glue 5 mm: bottom and right" in pages[0].captions[0].text
        assert "glue" not in pages[-1].captions[0].text, "the last tile has no flap"

    def test_a_single_tile_has_no_glue_edges_at_all(self, tmp_path):
        _, pages, _ = build(tmp_path, grid=(1, 1), px=(300, 200), glue_flap_mm=GLUE_FLAP_MM)
        assert not any(line.dashed for line in pages[0].lines)
        assert "glue" not in pages[0].captions[0].text

    def test_the_flap_shifts_the_image_because_its_room_is_reserved(self, tmp_path):
        """The trade for a guaranteed cut line: the tile moves up and left to make room.

        Tile size is untouched — only where it sits on the sheet — so the poster still
        comes out at the size the plan promises.
        """
        _, without, _ = build(tmp_path / "a", grid=(2, 2), glue_flap_mm=0.0)
        _, with_flap, _ = build(tmp_path / "b", grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        plain, flapped = without[0].images[0], with_flap[0].images[0]
        assert (plain.width_mm, plain.height_mm) == (flapped.width_mm, flapped.height_mm)
        assert flapped.y_mm == pytest.approx(plain.y_mm + GLUE_FLAP_MM / 2, abs=0.01)
        assert flapped.x_mm == pytest.approx(plain.x_mm - GLUE_FLAP_MM / 2, abs=0.01)

    def test_the_rule_stays_inside_the_printable_area(self, tmp_path):
        plan, pages, _ = build(tmp_path, grid=(2, 2), glue_flap_mm=GLUE_FLAP_MM)
        left, bottom, right, top = printable_bounds(plan)
        for line in pages[0].lines:
            assert min(line.xs_mm) >= left - 1e-6 and max(line.xs_mm) <= right + 1e-6
            assert min(line.ys_mm) >= bottom - 1e-6 and max(line.ys_mm) <= top + 1e-6


class TestNoRoomForMarks:
    """A tile filling both axes leaves nowhere for any arm; the run says so."""

    def test_no_marks_are_drawn_and_a_warning_is_returned(self, tmp_path):
        image = Image.new("RGB", (374, 545), "slategrey")
        plan = plan_layout(
            width_px=374,
            height_px=545,
            dpi=50.0,
            dpi_source="test",
            paper_mm=A4,
            margins=UNIFORM,
            caption_strip_mm=0.0,
            orientation=Orientation.portrait,
            grid=(1, 1),
        )
        band_x, band_y = plan.page.band_mm(plan.tile_width_mm, plan.tile_height_mm)
        assert max(band_x, band_y) < 1.5, "the fixture must be tight on both axes"

        written = write_tiles(image, plan, tmp_path, "tight", 90)
        path = tmp_path / "tight.pdf"
        warnings = build_pdf(plan, written, path, "tight")

        assert read_pages(path)[0].lines == []
        assert len(warnings) == 1
        assert "no room for cut marks" in warnings[0]
        assert "--margin" in warnings[0], "the warning must name a way out"


class TestGuidePageText:
    """The guide's footer wraps. It used to be drawn as one long unwrapped string, and on
    a real 150x150 poster the glue legend ran off the right-hand edge mid-word."""

    def guide_page(self, tmp_path, **kwargs):
        image = Image.new("RGB", (600, 400), "slategrey")
        plan = plan_layout(
            width_px=600,
            height_px=400,
            dpi=50.0,
            dpi_source="test",
            paper_mm=A4,
            margins=UNIFORM,
            caption_strip_mm=CAPTION_STRIP_MM,
            orientation=Orientation.portrait,
            grid=(2, 2),
            **kwargs,
        )
        written = write_tiles(image, plan, tmp_path, "g", 90)
        guide = tmp_path / "guide.png"
        guide_image(image, plan).save(guide)
        path = tmp_path / "g_print.pdf"
        build_pdf(plan, written, path, "g", guide)
        return plan, read_pages(path)[0]

    def test_every_footer_line_fits_between_the_margins(self, tmp_path):
        plan, page = self.guide_page(tmp_path, glue_flap_mm=GLUE_FLAP_MM)
        left, _, right, _ = printable_bounds(plan)
        assert page.captions, "the guide page carries text"
        for caption in page.captions:
            assert caption.left_mm >= left - 1e-6
            assert caption.right_mm <= right + 1e-6, (
                f"{caption.text!r} overruns the right margin by {caption.right_mm - right:.1f} mm"
            )

    def test_the_legend_is_wrapped_rather_than_truncated(self, tmp_path):
        """Wrapping must not lose words — the old version simply ran off the page."""
        _, page = self.guide_page(tmp_path, glue_flap_mm=GLUE_FLAP_MM)
        rendered = " ".join(c.text for c in page.captions)
        for phrase in ("Solid corner marks", "slide that blank", "outward from r01c01"):
            assert phrase in rendered

    def test_no_legend_without_a_flap(self, tmp_path):
        _, page = self.guide_page(tmp_path, glue_flap_mm=0.0)
        rendered = " ".join(c.text for c in page.captions)
        assert "glue edge" not in rendered
        assert "Print every page at 100%" in rendered

    def test_the_map_still_fits_above_the_footer(self, tmp_path):
        """A taller footer must push the map up, not overlap it."""
        _, page = self.guide_page(tmp_path, glue_flap_mm=GLUE_FLAP_MM)
        lowest_text = min(c.baseline_mm for c in page.captions)
        assert page.images, "the guide draws the whole poster"
        assert page.images[0].y_mm > lowest_text


class TestProbeSeesWholePdfs:
    """The probe itself, checked against a PDF that has a guide page.

    A guide page embeds a PNG, which is Flate-compressed exactly as the page streams are.
    Decoding alone therefore cannot tell them apart, and counting the image as a page put
    every page index out by one.
    """

    def test_a_guide_page_is_not_mistaken_for_a_page_or_an_image(self, tmp_path):
        image = Image.new("RGB", (600, 400), "slategrey")
        plan = plan_layout(
            width_px=600,
            height_px=400,
            dpi=50.0,
            dpi_source="test",
            paper_mm=A4,
            margins=UNIFORM,
            caption_strip_mm=CAPTION_STRIP_MM,
            orientation=Orientation.portrait,
            grid=(2, 2),
        )
        written = write_tiles(image, plan, tmp_path, "g", 90)
        guide = tmp_path / "guide.png"
        guide_image(image, plan).save(guide)
        path = tmp_path / "g_print.pdf"
        build_pdf(plan, written, path, "g", guide)

        pages = read_pages(path)
        assert len(pages) == plan.sheet_count + 1 == 5
        assert [len(page.images) for page in pages[1:]] == [1, 1, 1, 1]
        assert [page.captions[0].text[:6] for page in pages[1:]] == [
            "r01c01",
            "r01c02",
            "r02c01",
            "r02c02",
        ]


class TestPageSetup:
    def test_pages_are_the_requested_paper(self, tmp_path):
        _, _, path = build(tmp_path)
        assert media_boxes_mm(path) == {(210.0, 297.0)}

    def test_landscape_swaps_the_page(self, tmp_path):
        _, _, path = build(tmp_path, orientation=Orientation.landscape, grid=(2, 2))
        assert media_boxes_mm(path) == {(297.0, 210.0)}
