"""Read back what a generated PDF actually draws, in millimetres from the paper edge.

Every other way of checking a page is a lie by omission. A PDF viewer renders content that
sits in the printer's dead zone, and a test that recomputes a coordinate from the same
constants the code uses will agree with itself no matter what the code does. Both of the
worst bugs this project has had — a caption 2.5 mm from the paper edge, and tiles centred
on the page instead of the printable area — survived exactly that kind of checking.

So these helpers go to the PDF's own content stream and report where the ink landed.
reportlab writes one ASCII85 + Flate page stream per page. Embedded JPEGs are DCTDecode and
fail to decompress, but an embedded PNG is Flate like the pages are and decodes into
binary noise, so a page is recognised by the reset matrix reportlab opens every one with
rather than by decoding alone.
"""

from __future__ import annotations

import base64
import re
import zlib
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase.pdfmetrics import stringWidth

from imagegrid.pdf import LABEL_FONT
from imagegrid.units import MM_PER_INCH, PT_PER_INCH

_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
_MEDIA_BOX = re.compile(rb"/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]")
# "432 0 0 288 81.6 284.0 cm" followed by the XObject reference that uses it.
_IMAGE = re.compile(r"([\d.]+) 0 0 ([\d.]+) ([-\d.]+) ([-\d.]+) cm\s*\n/FormXob[^\s]+ Do")
# Dash state is part of the graphics state, so it is set by "d", pushed by "q" and
# restored by "Q". Tracking only "d" would let a dashed rule leak onto the solid corner
# marks drawn after it, which is exactly what happened once the marks stopped setting
# their own dash and started relying on the restore.
_OPS = re.compile(
    r"^(q)$"
    r"|^(Q)$"
    r"|^(\[[\d. ]*\]) 0 d$"
    r"|^n ([-\d.]+) ([-\d.]+) m ([-\d.]+) ([-\d.]+) l S$",
    re.M,
)
_TEXT = re.compile(r"BT 1 0 0 1 ([-\d.]+) ([-\d.]+) Tm \((.*?)\) Tj")
_PAGE_PREAMBLE = "1 0 0 1 0 0 cm"


_ESCAPE = re.compile(r"\\([0-7]{1,3}|.)", re.S)
_NAMED_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}


def pt_to_mm(pt: float | str) -> float:
    """Points to millimetres. Strings are accepted because every value here is a capture."""
    return float(pt) / PT_PER_INCH * MM_PER_INCH


def _unescape(literal: str) -> str:
    """Undo PDF string escaping, so a caption reads as the text that will be printed.

    reportlab writes non-ASCII as octal, and "\\267" is four characters in the stream but
    one middot on paper — measuring the raw form would overstate the caption's width.
    """

    def replace(match: re.Match[str]) -> str:
        body = match.group(1)
        if body and all(ch in "01234567" for ch in body):
            return chr(int(body, 8))
        return _NAMED_ESCAPES.get(body, body)

    return _ESCAPE.sub(replace, literal)


@dataclass(frozen=True)
class Rect:
    """A drawn image, positioned by its bottom-left corner."""

    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float

    @property
    def right_mm(self) -> float:
        return self.x_mm + self.width_mm

    @property
    def top_mm(self) -> float:
        return self.y_mm + self.height_mm


@dataclass(frozen=True)
class Line:
    x0_mm: float
    y0_mm: float
    x1_mm: float
    y1_mm: float
    dashed: bool = False

    @property
    def is_horizontal(self) -> bool:
        return abs(self.y0_mm - self.y1_mm) < 1e-6

    @property
    def xs_mm(self) -> tuple[float, float]:
        return (self.x0_mm, self.x1_mm)

    @property
    def ys_mm(self) -> tuple[float, float]:
        return (self.y0_mm, self.y1_mm)


@dataclass(frozen=True)
class Caption:
    """A drawn string, measured across the glyphs it will actually put on paper.

    ``left_mm`` is where the text starts: reportlab resolves ``drawCentredString`` itself
    and writes the left edge into the text matrix, so the stream never mentions the centre.
    """

    left_mm: float
    baseline_mm: float
    text: str

    @property
    def width_mm(self) -> float:
        return pt_to_mm(stringWidth(self.text, *LABEL_FONT))

    @property
    def right_mm(self) -> float:
        return self.left_mm + self.width_mm

    @property
    def centre_mm(self) -> float:
        return self.left_mm + self.width_mm / 2


@dataclass(frozen=True)
class PdfPage:
    images: list[Rect]
    lines: list[Line]
    captions: list[Caption]

    def ink_extent_mm(self) -> tuple[float, float, float, float]:
        """Bounding box of everything drawn: (left, bottom, right, top).

        Captions count across their full glyph width, not just their anchor, so a long
        filename that pushes the text past a side margin shows up here.
        """
        xs: list[float] = []
        ys: list[float] = []
        for image in self.images:
            xs += [image.x_mm, image.right_mm]
            ys += [image.y_mm, image.top_mm]
        for line in self.lines:
            xs += list(line.xs_mm)
            ys += list(line.ys_mm)
        for caption in self.captions:
            xs += [caption.left_mm, caption.right_mm]
            ys.append(caption.baseline_mm)
        return (min(xs), min(ys), max(xs), max(ys))


def _read_lines(content: str) -> list[Line]:
    """Stroked segments, carrying the dash state in force when each was drawn."""
    lines: list[Line] = []
    dashed = False
    stack: list[bool] = []
    for match in _OPS.finditer(content):
        push, pop, pattern, x0, y0, x1, y1 = match.groups()
        if push:
            stack.append(dashed)
        elif pop:
            dashed = stack.pop() if stack else False
        elif pattern:
            dashed = pattern.strip("[]").strip() != ""
        else:
            lines.append(
                Line(pt_to_mm(x0), pt_to_mm(y0), pt_to_mm(x1), pt_to_mm(y1), dashed=dashed)
            )
    return lines


def read_pages(path: Path) -> list[PdfPage]:
    """Every page's drawing operations, in page order."""
    pages: list[PdfPage] = []
    for blob in _STREAM.findall(path.read_bytes()):
        try:
            content = zlib.decompress(base64.a85decode(blob.strip(), adobe=True)).decode("latin-1")
        except Exception:
            continue  # an embedded JPEG: DCTDecode, not Flate
        if not content.startswith(_PAGE_PREAMBLE):
            continue  # a Flate-compressed image, not a page
        pages.append(
            PdfPage(
                images=[
                    Rect(pt_to_mm(x), pt_to_mm(y), pt_to_mm(w), pt_to_mm(h))
                    for w, h, x, y in _IMAGE.findall(content)
                ],
                lines=_read_lines(content),
                captions=[
                    Caption(pt_to_mm(x), pt_to_mm(y), _unescape(text))
                    for x, y, text in _TEXT.findall(content)
                ],
            )
        )
    return pages


def media_boxes_mm(path: Path) -> set[tuple[float, float]]:
    """The distinct page sizes the PDF declares."""
    return {
        (round(pt_to_mm(w), 3), round(pt_to_mm(h), 3))
        for w, h in _MEDIA_BOX.findall(path.read_bytes())
    }
