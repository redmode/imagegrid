"""Shared fixtures: real posters, each kept for a property a synthetic image would lack.

These are real photographs, downscaled. Each is a poster the tool got something wrong on,
and each was found by printing, not by reasoning — so they are kept rather than replaced
with generated images.

They are stored at 30-50 dpi, which is far below anything printable but leaves every
property under test intact: layout follows the physical size in millimetres, not the pixel
count, so a low-resolution copy of the same poster plans identically. The dpi differs per
file so the resolution reader sees more than one value. Each was chosen by checking that
the grid, orientation and sheet count come out the same as on the full-size original, both
with and without a glue flap; the physical size drifts by under 0.3 mm. Together they cost
2 MB instead of 53 MB.

Most tests need only a header, so they open lazily and never decode pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class Poster:
    """A fixture image and the facts about it that tests are allowed to rely on."""

    filename: str
    width_px: int
    height_px: int
    dpi: float
    kept_for: str

    @property
    def path(self) -> Path:
        return FIXTURES / self.filename

    @property
    def width_mm(self) -> float:
        return self.width_px / self.dpi * 25.4

    @property
    def height_mm(self) -> float:
        return self.height_px / self.dpi * 25.4


# fmt: off
# The columns are a table: filename, width px, height px, dpi, why it is kept.
POSTERS = (
    Poster(
        "poster_100x80cm.jpg", 1181, 945, 30.0,
        "a width no grid divides evenly — the remainder floor division would lose",
    ),
    Poster(
        "poster_130x95cm.jpg", 1791, 1309, 35.0,
        "reserving the glue flap costs it two sheets, 28 -> 30",
    ),
    Poster(
        "poster_105x150cm.jpg", 1653, 2362, 40.0,
        "portrait, and its guide legend was the one that ran off the page",
    ),
    Poster(
        "poster_72x50cm.jpg", 1276, 886, 45.0,
        "at full size its band fell 0.021 mm short of the flap, losing a cut line",
    ),
    Poster(
        "poster_37x50cm.jpg", 984, 728, 50.0,
        "sits a fraction inside a two-row fit, so reserving tips it to three",
    ),
)

# fmt: on

SAMPLE = POSTERS[0]


@pytest.fixture(scope="session")
def sample_path() -> Path:
    if not SAMPLE.path.exists():
        pytest.skip(f"sample image missing: {SAMPLE.path}")
    return SAMPLE.path


@pytest.fixture(scope="session")
def sample_image(sample_path: Path):
    """The main sample, fully decoded — the only fixture whose pixels are inspected."""
    from imagegrid.tiles import open_image

    return open_image(sample_path)


@pytest.fixture(params=POSTERS, ids=lambda poster: poster.filename)
def poster(request) -> Poster:
    """Each real poster in turn, as metadata. Open it lazily if you need the header."""
    if not request.param.path.exists():
        pytest.skip(f"fixture missing: {request.param.path}")
    return request.param
