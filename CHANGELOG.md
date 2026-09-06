# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-09-06

### Added

- Packaging metadata for PyPI: license, authors, keywords, classifiers and project URLs.
- MIT `LICENSE` file.
- This changelog.
- GitHub Actions CI running ruff, ty and pytest on Python 3.11 through 3.14.
- GitHub Actions release workflow: a `v*` tag builds, tests and publishes a GitHub release
  with the sdist and wheel attached.
- `RELEASING.md` describing the release process.
- `ruff format` is now enforced in CI; the codebase was formatted to match.

### Changed

- The distribution is named `imagegrid-cli`, because `imagegrid` on PyPI is occupied by an
  unrelated 2018 package. The import package and the installed command are still
  `imagegrid`.

### Notes

- Not on PyPI yet. Releases are installable from the GitHub tag or release assets; the
  build is already PyPI-valid and publishing will be enabled once the CLI settles.

## [0.8.1] - 2026-09-04

### Added

- Initial public release.

[Unreleased]: https://github.com/redmode/imagegrid/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/redmode/imagegrid/releases/tag/v0.9.0
[0.8.1]: https://github.com/redmode/imagegrid/releases/tag/v0.8.1
