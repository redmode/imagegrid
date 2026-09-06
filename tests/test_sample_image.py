"""End-to-end tests over the real sample photograph.

These are the tests that would catch a regression the pure-math tests cannot: DPI
actually read off a real JFIF header, pixels actually cropped, DPI actually written
back out, and a PDF whose pages are really A4.
"""

from __future__ import annotations

import re

import pytest
from PIL import Image, ImageChops

from conftest import SAMPLE
from imagegrid.layout import Margins, plan_layout
from imagegrid.pdf import build_pdf
from imagegrid.resolution import resolve_dpi
from imagegrid.tiles import guide_image, write_tiles
from imagegrid.units import PAPERS, Orientation, mm_to_pt

# Taken from the fixture registry rather than restated, so re-encoding a fixture cannot
# leave this file quietly asserting the dimensions of an image that no longer exists.
# TestTheSampleItself checks the registry against the file itself.
SAMPLE_WIDTH_PX = SAMPLE.width_px
SAMPLE_HEIGHT_PX = SAMPLE.height_px
SAMPLE_DPI = SAMPLE.dpi
SAMPLE_WIDTH_MM = SAMPLE.width_mm
SAMPLE_HEIGHT_MM = SAMPLE.height_mm


def _plan(image, *, grid: tuple[int, int] | None = (4, 5)):
    res = resolve_dpi(image)
    return plan_layout(
        width_px=image.width, height_px=image.height,
        dpi=res.dpi, dpi_source=res.source,
        paper_mm=PAPERS["a4"], margins=Margins.uniform(10.0), grid=grid,
    )


class TestTheSampleItself:
    """Guards the fixture: if these fail, the file changed, not the code."""

    def test_dimensions(self, sample_image):
        assert (sample_image.width, sample_image.height) == (SAMPLE_WIDTH_PX, SAMPLE_HEIGHT_PX)

    def test_width_is_not_evenly_divisible(self):
        assert SAMPLE_WIDTH_PX % 4 != 0, "the remainder is the point of this fixture"

    def test_carries_jfif_dpi(self, sample_image):
        res = resolve_dpi(sample_image)
        assert res.dpi == SAMPLE_DPI
        assert res.source == "JFIF"

    def test_is_progressive(self, sample_image):
        assert sample_image.info.get("progressive"), "tile encoding should mirror the source"


class TestPlanningTheSample:
    def test_physical_size_matches_the_filename(self, sample_image):
        """1000 x 800 mm, read from the file's own header rather than a literal."""
        plan = _plan(sample_image)
        assert plan.width_mm == pytest.approx(SAMPLE_WIDTH_MM, abs=0.1)
        assert plan.height_mm == pytest.approx(SAMPLE_HEIGHT_MM, abs=0.1)

    def test_the_real_file_plans_exactly_as_its_literals_do(self, sample_image):
        """The linkage, not the arithmetic.

        Every grid, fit and orientation result for this geometry is already proved in
        test_layout against literal inputs. What only the real file can show is that its
        header feeds plan_layout the same numbers — so this asserts the two plans are
        identical rather than restating those results.
        """
        from_file = _plan(sample_image)
        from_literals = plan_layout(
            width_px=SAMPLE_WIDTH_PX, height_px=SAMPLE_HEIGHT_PX, dpi=SAMPLE_DPI,
            dpi_source="literal", paper_mm=PAPERS["a4"],
            margins=Margins.uniform(10.0), grid=(4, 5),
        )
        assert (from_file.cols, from_file.rows) == (from_literals.cols, from_literals.rows)
        assert from_file.page == from_literals.page
        assert from_file.tiles == from_literals.tiles
        assert from_file.fits and from_file.page.orientation is Orientation.landscape


@pytest.fixture(scope="module")
def cut_result(sample_image, tmp_path_factory):
    out = tmp_path_factory.mktemp("tiles")
    plan = _plan(sample_image)
    return plan, write_tiles(sample_image, plan, out, "sample", 100), out


@pytest.fixture(scope="module")
def pdf(sample_image, tmp_path_factory):
    out = tmp_path_factory.mktemp("pdf")
    plan = _plan(sample_image)
    written = write_tiles(sample_image, plan, out, "sample", 100)
    guide = out / "guide.png"
    guide_image(sample_image, plan).save(guide)
    pdf_path = out / "print.pdf"
    warnings = build_pdf(plan, written, pdf_path, "sample", guide)
    return plan, pdf_path, warnings


class TestCuttingTheSample:
    def test_writes_every_tile(self, cut_result):
        plan, written, _ = cut_result
        assert len(written) == 20
        assert all(path.exists() for _, path in written)

    def test_tiles_reassemble_to_the_original(self, cut_result):
        _, written, _ = cut_result
        row0 = [(t, p) for t, p in written if t.row == 0]
        assert sum(Image.open(p).width for _, p in row0) == SAMPLE_WIDTH_PX
        col0 = [(t, p) for t, p in written if t.col == 0]
        assert sum(Image.open(p).height for _, p in col0) == SAMPLE_HEIGHT_PX

    def test_the_remainder_pixel_is_kept_not_dropped(self, cut_result):
        """The point of this fixture: its width divides into a remainder, never evenly."""
        _, written, _ = cut_result
        row = [(t, p) for t, p in written if t.row == 0]
        widths = sorted({Image.open(p).width for _, p in row})
        assert len(widths) == 2 and widths[1] - widths[0] == 1, (
            f"expected two widths one pixel apart, got {widths}"
        )
        assert sum(Image.open(p).width for _, p in row) == SAMPLE_WIDTH_PX

    def test_every_tile_records_the_print_resolution(self, cut_result):
        """Without this, "print at 100%" lands at the wrong physical size."""
        _, written, _ = cut_result
        for _, path in written:
            assert Image.open(path).info.get("dpi") == (SAMPLE_DPI, SAMPLE_DPI)

    def test_tile_content_matches_the_source_region(self, sample_image, cut_result):
        """A JPEG round trip is never byte-identical, so compare within a small tolerance.

        The point is that the *right* region was cut: a tile taken from the wrong place
        differs by far more than any encoding noise ever could.
        """
        _, written, _ = cut_result
        tile, path = written[7]
        expected = sample_image.crop(tile.box).convert("L")
        actual = Image.open(path).convert("L")
        assert actual.size == expected.size
        diff = ImageChops.difference(actual, expected)
        mean = sum(i * n for i, n in enumerate(diff.histogram())) / (diff.width * diff.height)
        assert mean < 2.0, f"mean pixel difference {mean:.2f} — wrong region, not jpeg noise"

    def test_filenames_are_row_then_column(self, cut_result):
        _, written, _ = cut_result
        assert written[0][1].name == "sample_r01c01.jpg"
        assert written[-1][1].name == "sample_r05c04.jpg"


class TestPdfFromTheSample:
    def test_is_written_without_warnings(self, pdf):
        _, path, warnings = pdf
        assert path.exists() and path.stat().st_size > 0
        assert warnings == []

    def test_has_one_page_per_tile_plus_the_guide(self, pdf):
        plan, path, _ = pdf
        assert path.read_bytes().count(b"/Type /Page\n") == plan.sheet_count + 1

    def test_every_page_is_a4_landscape(self, pdf):
        plan, path, _ = pdf
        boxes = re.findall(rb"/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]", path.read_bytes())
        assert len(boxes) == plan.sheet_count + 1
        for w, h in boxes:
            assert float(w) == pytest.approx(mm_to_pt(297.0), abs=0.01)
            assert float(h) == pytest.approx(mm_to_pt(210.0), abs=0.01)


class TestGuideFont:
    def test_the_labels_survive_a_machine_with_none_of_the_named_fonts(self, monkeypatch):
        """On Linux none of the macOS paths exist; the guide must still be legible."""
        import imagegrid.tiles as tiles

        monkeypatch.setattr(tiles, "_FONT_CANDIDATES", ("/no/such/font.ttf",))
        image = Image.new("RGB", (800, 400), "seagreen")
        plan = plan_layout(
            width_px=800, height_px=400, dpi=100.0, dpi_source="test",
            paper_mm=PAPERS["a4"], margins=Margins.uniform(10.0), grid=(2, 1),
        )
        guide = tiles.guide_image(image, plan, max_px=400)
        assert guide.size == (400, 200), "a missing font must not stop the guide rendering"


class TestColourProfile:
    def test_an_icc_profile_is_carried_onto_every_tile(self, tmp_path):
        """Dropping the profile would shift the colours of a print silently."""
        from PIL import ImageCms

        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        source = tmp_path / "tagged.jpg"
        Image.new("RGB", (200, 100), "seagreen").save(source, dpi=(100, 100), icc_profile=profile)

        image = Image.open(source)
        image.load()
        plan = plan_layout(
            width_px=image.width, height_px=image.height, dpi=100.0, dpi_source="test",
            paper_mm=PAPERS["a4"], margins=Margins.uniform(10.0), grid=(2, 1),
        )
        for _, path in write_tiles(image, plan, tmp_path, "tagged", 90):
            assert Image.open(path).info.get("icc_profile"), f"{path.name} lost its profile"


class TestGuideImage:
    def test_is_downscaled_and_keeps_the_aspect_ratio(self, sample_image):
        plan = _plan(sample_image)
        guide = guide_image(sample_image, plan, max_px=800)
        assert max(guide.size) == 800
        assert guide.width / guide.height == pytest.approx(
            SAMPLE_WIDTH_PX / SAMPLE_HEIGHT_PX, abs=0.01
        )
