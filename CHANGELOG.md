# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Releases are cut as git tags; see [RELEASING.md](RELEASING.md).

## [Unreleased]

## [0.9.0] - 2026-09-06

First packaged release. No change to how the tool works — this makes it installable,
testable on every supported platform, and releasable from a tag.

### Added

- Complete package metadata: MIT `LICENSE`, authors, keywords, classifiers and project
  URLs, so the build is publishable as-is.
- Continuous integration on Linux, macOS and Windows across Python 3.11 to 3.14, running
  `ruff check`, `ruff format --check`, `ty` and the test suite, plus a build check that
  validates the artifacts and smoke-tests the wheel in a clean virtualenv.
- A release workflow: pushing a `vX.Y.Z` tag verifies the tag matches `__version__` and
  that this file has a section for it, runs the full suite, builds the sdist and wheel,
  and publishes a GitHub release with both attached.
- `RELEASING.md` and this changelog.

### Changed

- The distribution builds as `imagegrid-cli`, because `imagegrid` on PyPI is held by an
  unrelated package last released in 2018. The import package and the installed command
  are both still `imagegrid`; the name differs only at install time.
- The source distribution no longer carries `tests/fixtures/` — 2.2 MB of JPEGs that would
  dominate the download. The bundled tests skip the cases whose fixtures are absent.
- The codebase is formatted with `ruff format`, now enforced in CI.

### Fixed

- A truncated sentence in the glue-flap section of the README, and an example output block
  that no longer matched what the CLI prints.

### Notes

- Not published to PyPI yet. Install from a tag or from the wheel attached to a release;
  see the README. The build is already PyPI-valid and `twine check --strict` runs on every
  release, so publishing later is an additive change.

## [0.8.1] - 2026-09-04

### Added

- Initial release: split an image into print-ready tiles at exact 100% scale, with
  millimetre-based layout driven by the image's own resolution, auto-fit or forced grids,
  per-sheet cut marks and captions, glue flaps, an assembly guide page, and a PDF that
  pins every tile to its exact physical size.

[Unreleased]: https://github.com/redmode/imagegrid/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/redmode/imagegrid/releases/tag/v0.9.0
[0.8.1]: https://github.com/redmode/imagegrid/releases/tag/v0.8.1
