"""Tests for the DPI resolution chain, which decides the poster's physical size."""

from __future__ import annotations

import pytest

from imagegrid.resolution import (
    PrintSize,
    Resolution,
    ResolutionError,
    dpi_for_print_size,
    resolve_dpi,
)


class FakeImage:
    """Stands in for a Pillow image: only ``info`` and ``getexif`` are consulted."""

    def __init__(self, info=None, exif=None):
        self.info = info or {}
        self._exif = exif

    def getexif(self):
        return self._exif if self._exif is not None else {}


TAG_X, TAG_Y, TAG_UNIT = 0x011A, 0x011B, 0x0128


def test_explicit_override_wins():
    img = FakeImage({"jfif_unit": 1, "jfif_density": (100, 100)})
    res = resolve_dpi(img, override=300)
    assert res.dpi == 300.0
    assert res.source == "--dpi"


def test_rejects_non_positive_override():
    with pytest.raises(ResolutionError):
        resolve_dpi(FakeImage(), override=0)


def test_reads_jfif_dpi():
    # Exactly what the real poster_100x80cm.jpg fixture carries.
    res = resolve_dpi(FakeImage({"jfif_unit": 1, "jfif_density": (100, 100), "dpi": (100, 100)}))
    assert res.dpi == 100.0
    assert res.source == "JFIF"


def test_converts_jfif_dots_per_centimetre():
    res = resolve_dpi(FakeImage({"jfif_unit": 2, "jfif_density": (40, 40)}))
    assert res.dpi == pytest.approx(101.6)


def test_jfif_aspect_ratio_only_is_not_a_resolution():
    """unit=0 means "pixel aspect ratio"; density 1 is not one dot per inch."""
    with pytest.raises(ResolutionError):
        resolve_dpi(FakeImage({"jfif_unit": 0, "jfif_density": (1, 1), "dpi": (1, 1)}))


def test_falls_back_to_pillow_dpi_key():
    res = resolve_dpi(FakeImage({"dpi": (300, 300)}))
    assert res.dpi == 300.0
    assert res.source == "image metadata"


def test_falls_back_to_exif_inches():
    res = resolve_dpi(FakeImage(exif={TAG_X: 240, TAG_Y: 240, TAG_UNIT: 2}))
    assert res.dpi == 240.0
    assert res.source == "EXIF"


def test_converts_exif_centimetres():
    res = resolve_dpi(FakeImage(exif={TAG_X: 100, TAG_Y: 100, TAG_UNIT: 3}))
    assert res.dpi == pytest.approx(254.0)


def test_exif_without_absolute_unit_is_ignored():
    with pytest.raises(ResolutionError):
        resolve_dpi(FakeImage(exif={TAG_X: 72, TAG_Y: 72, TAG_UNIT: 1}))


def test_corrupt_exif_does_not_crash_the_run():
    class Broken(FakeImage):
        def getexif(self):
            raise ValueError("bad exif block")

    with pytest.raises(ResolutionError):
        resolve_dpi(Broken())


def test_missing_resolution_names_the_fix():
    with pytest.raises(ResolutionError, match="--dpi"):
        resolve_dpi(FakeImage())


def test_non_square_pixels_are_refused():
    """A file claiming different X and Y densities would print distorted; say so."""
    with pytest.raises(ResolutionError, match=r"(?s)300 x 150 dpi.*--dpi"):
        resolve_dpi(FakeImage({"dpi": (300, 150)}))


def test_rounding_level_density_mismatch_is_tolerated():
    """PNG pHYs stores pixels per metre, so 100 dpi can read back as 99.9998 on one axis."""
    assert resolve_dpi(FakeImage({"dpi": (100.0, 99.9998)})).dpi == 100.0


def test_override_bypasses_the_square_check():
    assert resolve_dpi(FakeImage({"dpi": (300, 150)}), override=300).dpi == 300.0


def test_a_non_numeric_density_is_treated_as_missing():
    """Some files carry junk where a density should be; that is "unknown", not a crash."""
    with pytest.raises(ResolutionError, match="--dpi"):
        resolve_dpi(FakeImage({"dpi": ("high", "high")}))


def test_a_half_written_exif_resolution_is_ignored():
    """X present, Y absent — nothing usable, so fall through to the error."""
    with pytest.raises(ResolutionError, match="--dpi"):
        resolve_dpi(FakeImage(exif={TAG_X: 300, TAG_UNIT: 2}))


def test_readers_stop_at_the_first_answer(monkeypatch):
    """The chain is ordered by authority, so a later reader must not run once one answers."""
    import imagegrid.resolution as resolution

    invoked: list[str] = []

    def spy(name, func):
        def wrapper(image):
            invoked.append(name)
            return func(image)

        return wrapper

    monkeypatch.setattr(
        resolution,
        "_READERS",
        tuple(spy(f.__name__, f) for f in resolution._READERS),
    )
    res = resolution.resolve_dpi(FakeImage({"jfif_unit": 1, "jfif_density": (100, 100)}))
    assert res.source == "JFIF"
    assert invoked == ["_from_jfif"], "EXIF must not be parsed once JFIF has answered"


def test_override_consults_no_reader(monkeypatch):
    import imagegrid.resolution as resolution

    def explode(image):
        raise AssertionError("no reader should run when --dpi is given")

    monkeypatch.setattr(resolution, "_READERS", (explode,))
    assert resolution.resolve_dpi(FakeImage(), override=300).dpi == 300.0


class TestPrintSize:
    """Deriving a resolution from a requested physical size."""

    # The real fixture: 1.24984 wide, named "100x80" (1.25) — a 0.013% mismatch.
    W_PX, H_PX = 3937, 3150

    def _dpi(self, **kwargs) -> Resolution:
        return dpi_for_print_size(self.W_PX, self.H_PX, PrintSize(**kwargs))

    def _size_mm(self, res: Resolution) -> tuple[float, float]:
        return (self.W_PX / res.dpi * 25.4, self.H_PX / res.dpi * 25.4)

    def test_width_alone_is_exact_and_height_follows(self):
        res = self._dpi(width_mm=1500.0)
        width, height = self._size_mm(res)
        assert width == pytest.approx(1500.0)
        assert height == pytest.approx(1500.0 * self.H_PX / self.W_PX)
        assert res.note is None, "a single axis is honoured exactly, so there is nothing to explain"

    def test_height_alone_is_exact_and_width_follows(self):
        res = self._dpi(height_mm=500.0)
        width, height = self._size_mm(res)
        assert height == pytest.approx(500.0)
        assert width == pytest.approx(500.0 * self.W_PX / self.H_PX)

    def test_both_axes_never_exceed_the_request(self):
        width, height = self._size_mm(self._dpi(width_mm=1000.0, height_mm=800.0))
        assert width <= 1000.0 + 1e-9
        assert height <= 800.0 + 1e-9

    def test_aspect_is_always_preserved(self):
        """A wildly wrong box must not stretch the photograph."""
        res = self._dpi(width_mm=1600.0, height_mm=900.0)
        width, height = self._size_mm(res)
        assert width / height == pytest.approx(self.W_PX / self.H_PX)
        assert height == pytest.approx(900.0), "the height binds first"
        assert width == pytest.approx(1124.85, abs=0.05), "the width comes out well under 1600"

    def test_note_names_the_binding_axis_when_the_box_is_not_filled(self):
        note = self._dpi(width_mm=1600.0, height_mm=900.0).note
        assert note is not None
        assert "1600 x 900 mm" in note and "height was binding" in note

    def test_note_appears_whenever_the_shortfall_is_visible_in_the_table(self):
        """The fixture's own 100x80cm name lands 0.125 mm short — which shows at one decimal.

        The plan table prints 999.9 x 800.0 for a request of 1000 x 800, so the note is
        what explains the discrepancy rather than leaving it looking like a rounding bug.
        """
        res = self._dpi(width_mm=1000.0, height_mm=800.0)
        width, _ = self._size_mm(res)
        assert 1000.0 - width == pytest.approx(0.125, abs=0.01)
        assert f"{width:.1f}" != "1000.0"
        assert res.note is not None and "width" not in res.note.split("kept aspect")[1]

    def test_no_note_when_the_request_is_met_to_within_display_precision(self):
        """An exact-aspect request has nothing to explain."""
        exact_height = 1000.0 * self.H_PX / self.W_PX
        assert self._dpi(width_mm=1000.0, height_mm=exact_height).note is None

    @pytest.mark.parametrize("kwargs", [{"width_mm": 0.0}, {"height_mm": -5.0}])
    def test_rejects_non_positive_sizes(self, kwargs):
        with pytest.raises(ResolutionError):
            PrintSize(**kwargs)

    def test_rejects_an_empty_request(self):
        with pytest.raises(ResolutionError):
            PrintSize()

    def test_label_describes_what_was_asked_for(self):
        assert PrintSize(1000.0, 800.0).label == "1000 x 800 mm"
        assert PrintSize(width_mm=1500.0).label == "1500 mm wide"
        assert PrintSize(height_mm=800.0).label == "800 mm tall"
