# imagegrid

[![CI](https://github.com/redmode/imagegrid/actions/workflows/ci.yml/badge.svg)](https://github.com/redmode/imagegrid/actions/workflows/ci.yml)

Split a large image into a grid of tiles, each sized to print on a single sheet at
**exact 100% scale**, then glue the pieces back together into a full-size poster.

```bash
imagegrid tests/fixtures/poster_100x80cm.jpg --grid 4x5
```

```
Source  1181x945 px @ 30 dpi (JFIF)  ->  999.9 x 800.1 mm
Paper   297x210 mm landscape, margin 10 mm  ->  printable 277 x 190 mm (less 5 mm for captions)
Grid    4 columns x 5 rows = 20 tiles (auto-fit would use 6x3 = 18 tiles (portrait))
Tile    295x189 px  ->  250.0 x 160.0 mm    fits, 22.0 x 20.0 mm to spare
Glue    5 mm flap on the bottom and right edges  (reserved before the grid, so its cut line always prints)
Input   tests/fixtures/poster_100x80cm.jpg
Output  out/poster_100x80cm
```

## Why it is not just a crop loop

- **Physical size is the unit of work.** The grid, the fit check and the PDF are all
  computed in millimetres, derived from the image's own recorded resolution. `imagegrid`
  refuses to run rather than assume a DPI, because a guessed DPI silently produces a
  poster of the wrong size.
- **Every tile carries its DPI.** That is what makes "100% / actual size" in a print
  dialog land on the right number of millimetres.
- **The PDF pins the geometry.** One sheet per tile, each placed at its own exact size,
  so the ~1 px rounding differences between tiles never accumulate across the poster.
- **Cut marks in the trim border.** Pieces are butt-joined — cut on the marks, then glue
  edge to edge. There is no overlap to absorb a sloppy cut, so the marks matter.
- **Nothing is lost at the seams.** Tile boundaries partition the image exactly; a
  3937 px wide image split five ways keeps all 3937 columns.

## Install

```bash
uv tool install git+https://github.com/redmode/imagegrid
```

That gives you a system-wide `imagegrid` command. Append `@vX.Y.Z` to pin a particular
release, or run `uv tool install .` from a checkout to install your working copy.

Not on PyPI yet — that comes once the command-line interface settles. Every release also
attaches a built wheel, on the
[releases page](https://github.com/redmode/imagegrid/releases/latest).

If the shim is not found afterwards, run `uv tool update-shell` once and open a new
shell — uv installs to `~/.local/bin`.

## Develop

```bash
uv sync                    # creates ./.venv from pyproject.toml + uv.lock
uv run imagegrid --help
uv run pytest -q
uv run ruff check .        # lint; --fix applies the safe ones
uv run ruff format .       # formatter; CI checks this, so run it before pushing
uv run ty check            # type check
```

Add dependencies with `uv add <pkg>` rather than `pip install`, so `uv.lock` stays honest.

CI runs the test suite on Linux, macOS and Windows across Python 3.11 to 3.14; the lint,
format and type checks run once, on Linux. Releases are cut as git tags — see
[RELEASING.md](RELEASING.md), and [CHANGELOG.md](CHANGELOG.md) for what changed when.

## Usage

```
imagegrid IMAGE [OPTIONS]

  -o, --output DIR         Output directory  [default: ./out/<image name>]
  -g, --grid COLSxROWS     Force a grid, columns first, e.g. 4x5  [default: auto-fit]
  -p, --paper NAME|SIZE    a4, a3, a5, letter, legal, tabloid, or e.g. 210x297mm  [a4]
      --orientation ...    auto | portrait | landscape  [auto]
  -m, --margin LENGTH      Unprintable border on all four sides  [10mm]
      --margin-top LENGTH      override --margin for one edge
      --margin-right LENGTH
      --margin-bottom LENGTH
      --margin-left LENGTH
      --dpi FLOAT          Print at this resolution instead of the image's own
  -s, --size WxH           Print at this finished size, e.g. 100x80cm
      --width LENGTH       Print this wide, e.g. 150cm; height follows the aspect
      --height LENGTH      Print this tall, e.g. 80cm; width follows the aspect
  -q, --quality INT        JPEG quality  [100]
      --tiles              Also write the individual tile images
      --no-guide           Omit the assembly guide page
      --no-caption         Omit the per-sheet label (and free up its strip)
      --glue-flap LENGTH   Blank flap on the bottom/right edges  [5mm]
      --no-glue-flap       Cut every edge exactly, with no flap
  -n, --dry-run            Show the plan and write nothing
      --version            Show the version and exit
```

Lengths accept `mm`, `cm`, `in`, `"` and `pt`; a bare number means millimetres.

### Margins

`--margin` is what the tool assumes your printer cannot reach. It drives the grid, keeps
the cut marks and captions where they will actually print, and is checked against every
tile. Most printers have a deeper dead zone on one edge, so any side can be overridden:

```bash
imagegrid photo.JPG --margin 10mm --margin-bottom 20mm
```

Tiles are centred in the **printable area**, not on the page — with asymmetric margins
those are different places, and centring on the page would push image into the dead zone
where it prints clipped. Sides are named for the page as you look at it, so `bottom` stays
the bottom in either orientation.

If a printed sheet comes out clipped, the margin is set smaller than your printer's real
dead zone; raise it.

Every sheet is labelled in its top border (`r02c03 · 250.0 x 160.0 mm · col 3/4 row 2/5`,
followed by the glue edges and the source file's name).
A 5 mm strip is reserved for that label **before** the grid is chosen, because auto-fit
maximises the tile to minimise sheets and would otherwise consume it — raising the bottom
margin does not help, it shrinks the leftover space instead. Reserving is usually free; on
an image whose height divides the printable height exactly it can add a row, and
`--no-caption` gives the space back.

### Glue flaps

A butt joint has nothing to glue: a paper edge is a tenth of a millimetre of surface. So
each sheet keeps a **blank 5 mm flap on its bottom and right edges**, which slides under
the neighbouring sheet and takes the adhesive:

```
  r02c03 · 250.0 x 160.0 mm · col 3/4 row 2/5 · glue 5 mm: bottom and right

   |                                |
  --            image             -- |
   |                                | |
  - - - - - - - - - - - - - - - - - -|- -  <- dashed: cut along it, keep the flap
   |                              --   --
                                    ^ corner marks sit on the piece, flap included
```

The dashed line is drawn where you actually cut, full length along both glue edges and
meeting at the outer corner, so it traces the whole piece and there is nothing to measure.
Solid corner marks bracket that same rectangle — the flap included, not the image — so a
blade lines up on them directly.

Room for the flap is reserved **before the grid is chosen**, exactly as the caption strip
is, which is what guarantees the cut line has somewhere to print. Taking it from whatever
white happened to be left over did not work: a tile can land flush against the flap, and
then that edge has no line at all. Reserving is free on most layouts and costs a row or a
column on one that was already close to a boundary.

Sheets in the last row and last column have no neighbour on those sides and are marked
solid all round. Assembly runs from `r01c01`, each new sheet laying **on top** of the flap
of the one before.

The flap is blank rather than a duplicate of the neighbour's pixels, so the visible seam is
still an exact butt joint and the finished poster keeps the size the plan promised.
`--no-glue-flap` returns to cutting every edge exactly, and gives the reserved space back
to the image.

### Print size

An image normally carries its own resolution, and that decides how big it prints. To
override it, say the size you want rather than doing the dpi arithmetic yourself:

```bash
imagegrid photo.JPG --width 150cm       # 1.5 m wide, height follows
imagegrid photo.JPG --size 100x80cm     # fit within 100 x 80 cm
imagegrid photo.JPG --dpi 300           # the underlying primitive, still there
```

**Aspect ratio is always preserved.** `--size` is therefore a bounding box, not a stretch:
whichever axis binds first sets the scale and the other lands at or under what you asked
for. Requesting `160x90cm` for a 5:4 photograph gives 112.5 x 90 cm, and the plan table
says so rather than silently squashing the image. These four options are mutually
exclusive — they all set the same thing.

One caveat: JPEG records resolution as a whole number of dpi, so a size that resolves to
a fractional dpi cannot be tagged exactly on the tile files. When `--tiles` is on,
`imagegrid` says so, with the resulting error in millimetres. The PDF is unaffected — it
places every tile at its exact size.

A file whose header claims different horizontal and vertical resolutions is refused, since
its pixels could not be square on paper; `--dpi` says which value to trust.

### Grids

Without `--grid`, `imagegrid` picks the **fewest sheets** that fit the paper, trying both
orientations. With `--grid`, it picks the orientation that fits your grid best, and tells
you what auto-fit would have done. A grid whose tiles cannot fit the sheet is an error
with the overflow in millimetres, not a silently scaled poster.

Note that the margin drives the grid. For the 1000 x 800 mm example above at 10 mm
margins, `5x4` overflows A4 by 10 mm in either orientation while `4x5` fits landscape
comfortably.

## Output

```
out/poster_100x80cm/
└── poster_100x80cm_print.pdf    assembly guide + one sheet per tile
```

The PDF already embeds every tile at its exact size, so the loose images are not written
by default — they are cut to a scratch directory and discarded. Add `--tiles` when you
want them on their own:

```
out/poster_100x80cm/
├── poster_100x80cm_r01c01.jpg   ... one per tile, tagged with the print DPI
└── poster_100x80cm_print.pdf
```

## Printing

1. Print the PDF at **100% / actual size**. Turn off "fit to page", "shrink to fit" and
   any borderless mode.
2. Check one sheet with a ruler before printing the rest.
3. Cut each sheet on its corner marks.
4. Lay the pieces out using the guide page and butt them edge to edge.

## License

MIT — see [LICENSE](LICENSE).
