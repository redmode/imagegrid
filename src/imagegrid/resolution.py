"""Work out the image's real print resolution.

This is the linchpin of the whole tool: physical size is pixels / dpi, so a wrong dpi
means a poster that silently prints at the wrong size. When the resolution genuinely
cannot be determined we raise instead of assuming a default.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .units import CM_PER_INCH, MM_PER_INCH

# EXIF tag numbers (see Exif 2.3 table 4).
_TAG_X_RESOLUTION = 0x011A
_TAG_Y_RESOLUTION = 0x011B
_TAG_RESOLUTION_UNIT = 0x0128

# Below this, a requested and achieved size are the same number once rounded for display.
_SIZE_NOTE_THRESHOLD_MM = 0.05

# A density of 1 is JFIF's "no absolute unit" placeholder, not one dot per inch.
_MIN_PLAUSIBLE_DENSITY = 1.0

# X and Y densities may differ by rounding (a PNG pHYs stores pixels per metre, so 100 dpi
# reads back as 99.9998) but not by intent: pixels are square on paper.
_SQUARE_REL_TOL = 1e-3


class ImageMetadata(Protocol):
    """The slice of a Pillow image this module reads — small enough to fake in tests.

    ``info`` is a read-only mapping with unconstrained keys because that is what Pillow
    actually offers — it keys some formats by tuple, not string — and this module only
    ever looks up a handful of names in it.
    """

    @property
    def info(self) -> Mapping[Any, Any]: ...

    def getexif(self) -> Any: ...


class ResolutionError(ValueError):
    """The image does not say how big it is meant to print, and nothing said otherwise."""


@dataclass(frozen=True)
class Resolution:
    """A resolved print resolution, and where it came from."""

    dpi: float
    source: str  # human-readable provenance, shown in the plan table
    note: str | None = None  # e.g. how a requested print size was reconciled


@dataclass(frozen=True)
class PrintSize:
    """A requested physical size. Either axis may be left open, but not both."""

    width_mm: float | None = None
    height_mm: float | None = None

    def __post_init__(self) -> None:
        for axis, value in (("width", self.width_mm), ("height", self.height_mm)):
            if value is not None and value <= 0:
                raise ResolutionError(f"print {axis} must be positive, got {value} mm")
        if self.width_mm is None and self.height_mm is None:
            raise ResolutionError("a print size needs at least one of width or height")

    @property
    def label(self) -> str:
        if self.width_mm is not None and self.height_mm is not None:
            return f"{self.width_mm:g} x {self.height_mm:g} mm"
        if self.width_mm is not None:
            return f"{self.width_mm:g} mm wide"
        return f"{self.height_mm:g} mm tall"


def dpi_for_print_size(width_px: int, height_px: int, size: PrintSize) -> Resolution:
    """The resolution that prints the image at, or within, a requested physical size.

    Aspect ratio is always preserved, so a two-axis request behaves as a bounding box:
    whichever axis binds first decides the scale, and the other comes out at or under
    what was asked for. That keeps a slightly-off request (a "100x80cm" name on an image
    whose true ratio is 1.24984) from quietly stretching the photograph.
    """
    per_axis = {
        "width": width_px / size.width_mm * MM_PER_INCH if size.width_mm else None,
        "height": height_px / size.height_mm * MM_PER_INCH if size.height_mm else None,
    }
    # A higher dpi packs the pixels tighter, so it prints smaller: the largest candidate
    # is the one that keeps the result inside every requested bound.
    binding, dpi = max(
        ((axis, value) for axis, value in per_axis.items() if value is not None),
        key=lambda item: item[1],
    )

    note = None
    if size.width_mm is not None and size.height_mm is not None:
        actual_w = width_px / dpi * MM_PER_INCH
        actual_h = height_px / dpi * MM_PER_INCH
        shortfall = max(size.width_mm - actual_w, size.height_mm - actual_h)
        if shortfall > _SIZE_NOTE_THRESHOLD_MM:
            note = f"requested {size.label}; kept aspect, {binding} was binding"
    return Resolution(dpi, "requested size", note)


def _plausible_density(value: object) -> tuple[float, float] | None:
    """Accept a 2-sequence of numbers, rejecting the placeholder densities.

    A JFIF file with ``units=0`` carries only a pixel aspect ratio, which Pillow can
    surface as a density of 1 — that is not one dot per inch, it is "unknown".
    """
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if x <= _MIN_PLAUSIBLE_DENSITY or y <= _MIN_PLAUSIBLE_DENSITY:
        return None
    return x, y


def _square_dpi(density: tuple[float, float], source: str) -> float:
    """Collapse an (x, y) density to one dpi, refusing a file that claims otherwise.

    Non-square pixels would mean a distorted print. The tool already declines to guess a
    missing resolution; a contradictory one gets the same treatment and the same fix.
    """
    x, y = density
    if not math.isclose(x, y, rel_tol=_SQUARE_REL_TOL):
        raise ResolutionError(
            f"{source} records different horizontal and vertical resolutions "
            f"({x:g} x {y:g} dpi), so the pixels would not be square on paper.\n"
            "Pass --dpi to say which one to use."
        )
    return x


def _from_jfif(image: ImageMetadata) -> Resolution | None:
    """Read the JFIF APP0 marker directly — the most trustworthy source for a JPEG."""
    unit = image.info.get("jfif_unit")
    density = _plausible_density(image.info.get("jfif_density"))
    if density is None or unit not in (1, 2):
        return None  # unit 0 means aspect ratio only, which tells us nothing about size
    if unit == 1:
        return Resolution(_square_dpi(density, "JFIF"), "JFIF")
    return Resolution(_square_dpi(density, "JFIF") * CM_PER_INCH, "JFIF (dpcm)")


def _from_info_dpi(image: ImageMetadata) -> Resolution | None:
    """Pillow's normalised ``dpi`` key — covers PNG pHYs and TIFF as well as JPEG."""
    density = _plausible_density(image.info.get("dpi"))
    if density is None:
        return None
    return Resolution(_square_dpi(density, "image metadata"), "image metadata")


def _from_exif(image: ImageMetadata) -> Resolution | None:
    """EXIF resolution tags — the last resort, and the only source recording its own unit."""
    try:
        exif = image.getexif()
    except Exception:  # a corrupt EXIF block should not sink the run
        return None
    if not exif:
        return None
    density = _plausible_density((exif.get(_TAG_X_RESOLUTION), exif.get(_TAG_Y_RESOLUTION)))
    if density is None:
        return None
    unit = exif.get(_TAG_RESOLUTION_UNIT, 2)
    if unit == 3:  # dots per centimetre
        return Resolution(_square_dpi(density, "EXIF") * CM_PER_INCH, "EXIF (dpcm)")
    if unit == 2:
        return Resolution(_square_dpi(density, "EXIF"), "EXIF")
    return None  # unit 1 is "no absolute unit"


# Most authoritative first; the first reader to answer wins and the rest never run.
_READERS = (_from_jfif, _from_info_dpi, _from_exif)


def resolve_dpi(image: ImageMetadata, override: float | None = None) -> Resolution:
    """Resolve print resolution, most authoritative source first.

    Raises :class:`ResolutionError` rather than guessing, naming ``--dpi`` as the fix.
    """
    if override is not None:
        if override <= 0:
            raise ResolutionError(f"--dpi must be positive, got {override}")
        return Resolution(float(override), "--dpi")

    for reader in _READERS:
        if found := reader(image):
            return found

    raise ResolutionError(
        "this image carries no print resolution, so its physical size is unknown.\n"
        "Say how big it should print with --size (e.g. --size 100x80cm), --width, "
        "--height, or --dpi."
    )
