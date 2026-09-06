"""Unit conversion, parsing, and the vocabulary shared across the package.

Millimetres are the internal unit for everything physical. Pixels only ever become
millimetres via a DPI, and points only exist at the PDF boundary.
"""

from __future__ import annotations

import re
from enum import StrEnum

MM_PER_INCH = 25.4
MM_PER_CM = 10.0
CM_PER_INCH = MM_PER_INCH / MM_PER_CM  # 2.54, for dots-per-cm metadata
PT_PER_INCH = 72.0


class Orientation(StrEnum):
    """How a sheet is turned. ``auto`` means "whichever suits the tiles better"."""

    auto = "auto"
    portrait = "portrait"
    landscape = "landscape"



def px_to_mm(px: float, dpi: float) -> float:
    return px / dpi * MM_PER_INCH


def mm_to_pt(mm: float) -> float:
    return mm / MM_PER_INCH * PT_PER_INCH


# Paper sizes as (width, height) in mm, portrait.
PAPERS: dict[str, tuple[float, float]] = {
    "a3": (297.0, 420.0),
    "a4": (210.0, 297.0),
    "a5": (148.0, 210.0),
    "letter": (215.9, 279.4),
    "legal": (215.9, 355.6),
    "tabloid": (279.4, 431.8),
}

_UNITS = {
    "mm": 1.0,
    "cm": MM_PER_CM,
    "in": MM_PER_INCH,
    '"': MM_PER_INCH,
    "pt": MM_PER_INCH / PT_PER_INCH,
}

_NUMBER = r"[0-9]*\.?[0-9]+"
_UNIT = r'(mm|cm|in|"|pt)'
_LENGTH_RE = re.compile(rf"^\s*({_NUMBER})\s*{_UNIT}?\s*$", re.IGNORECASE)
_SIZE_RE = re.compile(rf"^\s*({_NUMBER})\s*[x×]\s*({_NUMBER})\s*{_UNIT}?\s*$", re.IGNORECASE)
_GRID_RE = re.compile(r"^\s*(\d+)\s*[x×]\s*(\d+)\s*$")


class UnitError(ValueError):
    """A length, size, paper or grid string could not be parsed."""


def parse_length(text: str) -> float:
    """Parse ``"10mm"`` / ``"0.5in"`` / ``"1cm"`` / ``"12"`` into millimetres.

    A bare number is millimetres.
    """
    match = _LENGTH_RE.match(text)
    if not match:
        raise UnitError(f"cannot parse length {text!r} (expected e.g. '10mm', '0.5in', '12')")
    value, unit = float(match.group(1)), (match.group(2) or "mm").lower()
    return value * _UNITS[unit]


def parse_size(text: str) -> tuple[float, float]:
    """Parse ``"210x297mm"`` / ``"100x80cm"`` into a (width, height) pair in millimetres."""
    match = _SIZE_RE.match(text)
    if not match:
        raise UnitError(f"cannot parse size {text!r} (expected e.g. '210x297mm', '100x80cm')")
    factor = _UNITS[(match.group(3) or "mm").lower()]
    return float(match.group(1)) * factor, float(match.group(2)) * factor


def parse_paper(text: str) -> tuple[float, float]:
    """Resolve a paper name (``a4``) or an explicit size (``210x297mm``) to portrait mm."""
    key = text.strip().lower()
    if key in PAPERS:
        return PAPERS[key]
    try:
        width_mm, height_mm = parse_size(text)
    except UnitError:
        known = ", ".join(sorted(PAPERS))
        raise UnitError(
            f"unknown paper {text!r} (known: {known}; or a size like '210x297mm')"
        ) from None
    if width_mm <= 0 or height_mm <= 0:
        raise UnitError(f"paper dimensions must be positive, got {text!r}")
    return width_mm, height_mm


def parse_grid(text: str) -> tuple[int, int]:
    """Parse ``"4x5"`` as (columns, rows). Always COLUMNS first."""
    match = _GRID_RE.match(text)
    if not match:
        raise UnitError(f"cannot parse grid {text!r} (expected COLSxROWS, e.g. '4x5')")
    cols, rows = int(match.group(1)), int(match.group(2))
    if cols < 1 or rows < 1:
        raise UnitError(f"grid must be at least 1x1, got {text!r}")
    return cols, rows
