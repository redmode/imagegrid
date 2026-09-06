"""How options are parsed, combined and refused.

The layout maths is proved elsewhere; what these cover is the wiring between a typed
command line and it, plus every message a user can actually provoke. Those messages are
the whole interface when something is wrong, and none of them had a test.
"""

from __future__ import annotations

import re

import pytest
from PIL import Image
from typer.testing import CliRunner

from imagegrid import __version__
from imagegrid.cli import _resolve_margins, app

runner = CliRunner()


@pytest.fixture(scope="module")
def image(tmp_path_factory):
    """457 x 305 mm at 50 dpi — bigger than a sheet, so grids are interesting."""
    path = tmp_path_factory.mktemp("src") / "sample.jpg"
    Image.new("RGB", (900, 600), "slategrey").save(path, dpi=(50, 50))
    return path


def run(image, *args, out=None):
    argv = [str(image), *args]
    if out is not None:
        argv += ["-o", str(out)]
    return runner.invoke(app, argv)


def sheets_reported(output: str) -> int:
    match = re.search(r"=\s*(\d+) tiles", " ".join(output.split()))
    assert match is not None, f"no tile count in:\n{output}"
    return int(match.group(1))


def plan_line(output: str, label: str) -> str:
    """One row of the plan table, unwrapped — rich hard-wraps at the terminal width."""
    flat = " ".join(output.split())
    start = flat.index(label)
    return flat[start:]


class TestMarginResolution:
    def test_the_base_applies_to_every_side(self):
        margins = _resolve_margins("10mm", None, None, None, None)
        assert margins.is_uniform and margins.top == 10.0

    @pytest.mark.parametrize("side,index", [("top", 0), ("right", 1), ("bottom", 2), ("left", 3)])
    def test_each_override_wins_on_its_own_side_only(self, side, index):
        args = [None, None, None, None]
        args[index] = "25mm"
        margins = _resolve_margins("10mm", *args)
        assert getattr(margins, side) == 25.0
        others = [s for s in ("top", "right", "bottom", "left") if s != side]
        assert all(getattr(margins, other) == 10.0 for other in others)

    def test_overrides_carry_their_own_units(self):
        margins = _resolve_margins("10mm", "1cm", "0.5in", None, None)
        assert margins.top == 10.0
        assert margins.right == pytest.approx(12.7)
        assert margins.bottom == 10.0

    def test_a_bad_length_is_reported_not_crashed(self, image):
        result = run(image, "--margin-left", "banana", "--dry-run")
        assert result.exit_code == 1
        assert "cannot parse length" in result.output

    def test_margins_that_swallow_the_page_are_refused(self, image):
        result = run(image, "--margin-left", "150mm", "--margin-right", "150mm", "--dry-run")
        assert result.exit_code == 1
        assert "left and right" in " ".join(result.output.split())

    def test_the_table_shows_per_side_margins(self, image):
        result = run(image, "--margin-bottom", "20mm", "--dry-run")
        assert "margins t10 r10 b20 l10 mm" in plan_line(result.output, "Paper")

    def test_the_table_stays_terse_when_uniform(self, image):
        assert "margin 10 mm" in plan_line(run(image, "--dry-run").output, "Paper")


class TestScaleOptions:
    """--dpi, --size, --width and --height all set the same thing."""

    @pytest.mark.parametrize(
        "args,expected",
        [
            (["--dpi", "300", "--size", "40x30cm"], "--dpi and --size"),
            (["--dpi", "300", "--width", "40cm"], "--dpi and --width"),
            (["--size", "40x30cm", "--width", "40cm"], "--size already gives both axes"),
            (["--size", "40x30cm", "--height", "30cm"], "--size already gives both axes"),
        ],
    )
    def test_conflicting_options_are_refused(self, image, args, expected):
        result = run(image, *args, "--dry-run")
        assert result.exit_code == 1
        assert expected in " ".join(result.output.split())

    def test_width_alone_sets_the_finished_size(self, image):
        line = plan_line(run(image, "--width", "60cm", "--dry-run").output, "Source")
        assert "600.0 x 400.0 mm" in line

    def test_height_alone_sets_the_finished_size(self, image):
        line = plan_line(run(image, "--height", "30cm", "--dry-run").output, "Source")
        assert "450.0 x 300.0 mm" in line

    def test_size_is_a_bounding_box_and_says_so(self, image):
        """16:9 asked of a 3:2 image: the height binds and the note explains the shortfall."""
        output = " ".join(run(image, "--size", "160x90cm", "--dry-run").output.split())
        assert "1350.0 x 900.0 mm" in output
        assert "kept aspect, height was binding" in output

    def test_dpi_is_still_accepted(self, image):
        line = plan_line(run(image, "--dpi", "300", "--dry-run").output, "Source")
        assert "@ 300 dpi (--dpi)" in line

    def test_a_bad_size_is_reported(self, image):
        result = run(image, "--size", "huge", "--dry-run")
        assert result.exit_code == 1
        assert "cannot parse size" in result.output


class TestRefusals:
    def test_a_grid_that_cannot_fit_exits_with_advice(self, image):
        result = run(image, "--grid", "1x1", "--dry-run")
        assert result.exit_code == 1
        flat = " ".join(result.output.split())
        assert "does not fit" in flat and "over by" in flat
        assert "--orientation" in flat and "Omit --grid" in flat

    def test_the_overflow_is_quantified_on_both_axes(self, image):
        flat = " ".join(run(image, "--grid", "1x1", "--dry-run").output.split())
        assert "mm wide" in flat and "mm tall" in flat

    def test_an_unknown_paper_names_the_ones_it_knows(self, image):
        result = run(image, "--paper", "a9", "--dry-run")
        assert result.exit_code == 1
        assert "a4" in result.output and "letter" in result.output

    def test_an_image_without_a_resolution_names_every_way_out(self, tmp_path):
        path = tmp_path / "bare.jpg"
        Image.new("RGB", (400, 300), "navy").save(path)  # no dpi written
        result = run(path, "--dry-run")
        assert result.exit_code == 1
        flat = " ".join(result.output.split())
        assert all(flag in flat for flag in ("--size", "--width", "--height", "--dpi"))

    def test_a_missing_file_is_typers_own_error(self, tmp_path):
        assert run(tmp_path / "nope.jpg", "--dry-run").exit_code == 2

    def test_a_file_that_is_not_an_image_is_reported_cleanly(self, tmp_path):
        path = tmp_path / "not-a-photo.jpg"
        path.write_text("this is text, whatever the extension claims")
        result = run(path, "--dry-run")
        assert result.exit_code == 1
        assert "cannot read" in " ".join(result.output.split())


class TestGridOptions:
    def test_an_explicit_grid_reports_what_auto_fit_would_have_done(self, image):
        line = plan_line(run(image, "--grid", "3x2", "--dry-run").output, "Grid")
        assert "3 columns x 2 rows = 6 tiles" in line
        assert "auto-fit would use" in line

    def test_auto_fit_says_so(self, image):
        assert "(auto-fit)" in plan_line(run(image, "--dry-run").output, "Grid")

    def test_orientation_can_be_forced(self, image):
        line = plan_line(run(image, "--orientation", "landscape", "--dry-run").output, "Paper")
        assert "297x210 mm landscape" in line

    def test_paper_accepts_an_explicit_size(self, image):
        line = plan_line(
            run(image, "--paper", "100x150mm", "--orientation", "portrait", "--dry-run").output,
            "Paper",
        )
        assert "100x150 mm portrait" in line


class TestGlueFlapOption:
    def test_a_flap_is_reported_by_default(self, image):
        line = plan_line(run(image, "--dry-run").output, "Glue")
        assert "5 mm flap on the bottom and right edges" in line
        assert "reserved before the grid" in line

    def test_no_glue_flap_removes_the_row(self, image):
        assert "Glue" not in run(image, "--no-glue-flap", "--dry-run").output

    def test_the_width_can_be_set(self, image):
        line = plan_line(run(image, "--glue-flap", "8mm", "--dry-run").output, "Glue")
        assert "8 mm flap" in line

    def test_a_wide_flap_costs_sheets_rather_than_failing(self, image):
        """It is reserved like the caption strip, so a wide flap just needs a finer grid."""
        narrow = sheets_reported(run(image, "--glue-flap", "5mm", "--dry-run").output)
        wide = sheets_reported(run(image, "--glue-flap", "40mm", "--dry-run").output)
        assert wide > narrow

    def test_a_flap_that_swallows_the_page_is_refused(self, image):
        result = run(image, "--glue-flap", "200mm", "--dry-run")
        assert result.exit_code == 1
        flat = " ".join(result.output.split())
        assert "no room for a tile" in flat
        assert "--no-glue-flap" in flat, "the error must name a way out"

    def test_a_single_sheet_needs_no_flap_and_is_not_refused(self, image):
        """One tile has no neighbours, so even an absurd flap is irrelevant."""
        result = run(image, "--paper", "600x600mm", "--glue-flap", "40mm", "--dry-run")
        assert result.exit_code == 0
        assert "Glue" not in result.output

    def test_a_negative_flap_is_rejected_by_the_parser(self, image):
        result = run(image, "--glue-flap", "-5mm", "--dry-run")
        assert result.exit_code == 1


class TestCaptionOption:
    def test_the_strip_is_reserved_by_default(self, image):
        assert "less 5 mm for captions" in plan_line(run(image, "--dry-run").output, "Paper")

    def test_no_caption_gives_the_space_back(self, image):
        line = plan_line(run(image, "--no-caption", "--dry-run").output, "Paper")
        assert "captions" not in line


def test_version_prints_and_exits():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output
