"""Tests for the CLI's own decision-making, independent of Typer."""

from __future__ import annotations

import pytest

from imagegrid.cli import _tile_dpi_warning
from imagegrid.layout import Margins, plan_layout
from imagegrid.units import PAPERS, Orientation


def _plan(dpi: float):
    return plan_layout(
        width_px=3937, height_px=3150, dpi=dpi, dpi_source="test",
        paper_mm=PAPERS["a4"], margins=Margins.uniform(10.0),
        orientation=Orientation.portrait,
    )


class TestTileDpiWarning:
    """JPEG stores JFIF density as a whole number; a fractional dpi silently rounds."""

    def test_warns_when_a_fractional_dpi_would_shift_the_poster(self):
        # --width 150cm on this image resolves to 66.6665 dpi, tagged as 67.
        warning = _tile_dpi_warning(_plan(66.6665))
        assert warning is not None
        assert "67 dpi" in warning and "66.6665" in warning
        assert "7.5 mm narrower" in warning
        assert "the PDF is unaffected" in warning

    def test_silent_for_a_whole_dpi(self):
        assert _tile_dpi_warning(_plan(100.0)) is None

    def test_silent_when_the_rounding_drift_is_negligible(self):
        """A dpi a hair off a whole number moves the poster by far less than a cut can."""
        assert _tile_dpi_warning(_plan(100.0001)) is None

    @pytest.mark.parametrize("dpi,direction", [(66.6665, "narrower"), (67.4, "wider")])
    def test_names_the_direction_of_the_error(self, dpi, direction):
        warning = _tile_dpi_warning(_plan(dpi))
        assert warning is not None
        assert direction in warning
