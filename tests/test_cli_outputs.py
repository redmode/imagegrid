"""Which files a run actually produces, driven through the real command line.

These go through Typer rather than calling helpers, because the thing under test is the
option defaults themselves.
"""

from __future__ import annotations

import re

import pytest
from PIL import Image
from typer.testing import CliRunner

from imagegrid.cli import app

runner = CliRunner()


@pytest.fixture(scope="module")
def small_image(tmp_path_factory):
    """A tiny image with a real dpi, so a full run costs milliseconds."""
    path = tmp_path_factory.mktemp("src") / "sample.jpg"
    Image.new("RGB", (900, 600), "slategrey").save(path, dpi=(50, 50))
    return path


def run(image, out, *args):
    return runner.invoke(app, [str(image), "-o", str(out), *args])


def page_count(out) -> int:
    return (out / "sample_print.pdf").read_bytes().count(b"/Type /Page\n")


def sheets_reported(output: str) -> int:
    """Pull the tile count out of the plan table's Grid row."""
    match = re.search(r"=\s*(\d+) tiles", re.sub(r"\s+", " ", output))
    assert match is not None, f"no tile count in output:\n{output}"
    return int(match.group(1))


class TestDefaultOutputs:
    def test_default_writes_the_pdf_only(self, small_image, tmp_path):
        out = tmp_path / "default"
        assert run(small_image, out).exit_code == 0
        assert [p.name for p in out.iterdir()] == ["sample_print.pdf"]

    def test_no_intermediate_tiles_are_left_behind(self, small_image, tmp_path):
        out = tmp_path / "clean"
        run(small_image, out)
        assert not list(out.glob("*.jpg")), "tiles are cut to a scratch dir, not here"
        assert not list(out.glob("*guide*")), "the guide is a scratch file too"

    def test_tiles_opts_back_in(self, small_image, tmp_path):
        out = tmp_path / "with-tiles"
        assert run(small_image, out, "--tiles").exit_code == 0
        tiles = sorted(out.glob("*.jpg"))
        assert tiles, "--tiles must write the images"
        assert (out / "sample_print.pdf").exists(), "and still write the PDF"



    def test_tile_dpi_note_only_appears_with_tiles(self, small_image, tmp_path):
        """A 50 dpi image printed 33 cm wide lands on a fractional dpi."""
        quiet = run(small_image, tmp_path / "q", "--width", "33cm", "--dry-run")
        loud = run(small_image, tmp_path / "l", "--width", "33cm", "--dry-run", "--tiles")
        assert "tagged" not in quiet.output, "no tile files means nothing to warn about"
        assert "tagged" in loud.output

    def test_dry_run_writes_nothing_at_all(self, small_image, tmp_path):
        out = tmp_path / "dry"
        assert run(small_image, out, "--dry-run").exit_code == 0
        assert not out.exists()


class TestOtherDefaults:
    def test_guide_page_is_included_by_default(self, small_image, tmp_path):
        """One page per tile, plus the assembly guide."""
        out = tmp_path / "guide"
        result = run(small_image, out)
        assert page_count(out) == sheets_reported(result.output) + 1

    def test_no_guide_drops_exactly_one_page(self, small_image, tmp_path):
        out = tmp_path / "no-guide"
        result = run(small_image, out, "--no-guide")
        assert page_count(out) == sheets_reported(result.output)
