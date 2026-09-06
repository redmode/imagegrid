# Releasing

Releases are cut as **git tags on `main`**, and each tag produces a GitHub release with a
built sdist and wheel attached.

**PyPI publishing is deliberately not enabled yet.** A PyPI version number can never be
reused or replaced, so publishing while the command-line interface is still moving would
burn numbers and leave broken early versions installable forever. Everything else is
already in place — the metadata is complete, the build is validated with
`twine check --strict` on every release, and the wheel is smoke-tested. Turning PyPI on
later is purely additive; see [Enabling PyPI](#enabling-pypi-later) at the bottom.

The version lives in exactly one place: `__version__` in `src/imagegrid/__init__.py`.
Hatchling reads it from there, and the release workflow refuses to build if the git tag
disagrees with it.

## Naming

The distribution builds as **`imagegrid-cli`** because `imagegrid` on PyPI is held by an
unrelated package last released in 2018. The import package and the installed command are
both `imagegrid`; only the distribution name differs, and it is visible only at install
time.

## How work reaches a release

`main` is the trunk. It is the only long-lived branch, it must be releasable at every
commit, and it is what people install from — so every merge into it goes through a pull
request and has to pass the full check matrix. There is no separate `develop`: `main` is
the last release plus everything merged since.

This is enforced, not just conventional. `main` is protected: all fourteen checks must
pass, the branch must be up to date before merging, history stays linear, and force-pushes
and deletions are refused. Reviews are not required, so you can merge your own pull
requests.

What has been *released* is carried by **tags**, not by any branch. Immediately after a
release the tag and `main` point at the same commit; they diverge as the next features
land, and converge again at the next cut.

### Working on a feature

```bash
git switch main && git pull
git switch -c feat/tile-overlap
```

Build it, with tests. **Add the changelog line in the same pull request**, under
`## [Unreleased]`:

```markdown
## [Unreleased]

### Added

- `--overlap LENGTH` for printers that cannot butt-join cleanly.
```

Then open a pull request, let the checks run, merge, and delete the branch. Do not touch
`__version__` — it stays at the last released number until a release is cut.

Use `feat/` for new behaviour, `fix/` for bug fixes, `docs/` or `chore/` for the rest. The
prefix is a convention, not something enforced.

### Accumulating

Several features later, `## [Unreleased]` has written itself:

```markdown
## [Unreleased]

### Added

- `--overlap LENGTH` for printers that cannot butt-join cleanly.
- `--preview` writes a single contact-sheet PNG of the planned layout.

### Fixed

- A2 was missing from the paper size table.
```

That section is the whole reason releases are cheap: it is written while the work is fresh,
and the release workflow feeds it straight to `gh release create --notes-file`. The release
notes are what you wrote as you went, not something reconstructed from commit messages
afterwards.

### Deciding to cut

When the accumulated set is worth someone's attention — there is no required cadence — cut
a release, following [Cutting a release](#cutting-a-release) below.

`release/X.Y.Z` is a **chore branch, not a stabilisation branch**. In git-flow you cut a
release branch and then harden it for days while development continues elsewhere. Here
there is nothing to harden: `main` was already green, so the branch carries two mechanical
edits, lives for the length of one pull request, and is deleted. If you find yourself
fixing bugs on a release branch, something went wrong upstream; fix it on `main` and cut
again.

### Choosing the number

Read `## [Unreleased]`:

| It contains | Bump | Example |
| --- | --- | --- |
| anything under `Added` or `Changed` | minor | `0.9.0` -> `0.10.0` |
| only `Fixed` | patch | `0.9.0` -> `0.9.1` |

Before 1.0, a breaking change is allowed in a minor bump — that is what the leading zero
buys. Say so plainly in the changelog when it happens.

### Patching an older release

Not needed yet, and worth avoiding until it is. If a released version needs a fix while
`main` has already moved on, branch from the **tag** rather than from `main`:

```bash
git switch -c release/0.9.x v0.9.0
# fix, bump to 0.9.1, tag v0.9.1
```

then bring the fix forward to `main` with `git cherry-pick`. This is the one case where a
release branch is long-lived, and it only becomes worth the cost once people are running
more than one version.

## Cutting a release

Everything happens on a short-lived branch off `main`. The tag goes on `main` after the
merge, never on the branch.

```bash
git switch main && git pull
git switch -c release/X.Y.Z
```

On the branch, make exactly these edits:

1. Bump `__version__` in `src/imagegrid/__init__.py`.
2. In `CHANGELOG.md`, rename the `## [Unreleased]` heading to `## [X.Y.Z] - YYYY-MM-DD`,
   add a fresh empty `## [Unreleased]` above it, and update the link definitions at the
   bottom of the file. Nothing else moves — the entries were written as the work landed.

Nothing else belongs on this branch. If a fix is needed, it goes to `main` as its own pull
request first.

```bash
git commit -am "Release X.Y.Z"
gh pr create --fill
```

Opening the PR runs CI: ruff, ruff format, ty, and the test suite on Linux, macOS and
Windows across Python 3.11 through 3.14, plus a build check. When it is green:

```bash
gh pr merge --squash
git switch main && git pull
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

The tag push runs the **Release** workflow, which:

1. reads `__version__` and checks the tag matches it,
2. checks `CHANGELOG.md` has a `## [X.Y.Z]` section,
3. runs `uv sync --locked`, ruff, ruff format, ty and pytest,
4. builds the sdist and wheel with `uv build`,
5. validates them with `twine check --strict` — kept green so the metadata stays
   PyPI-ready even though nothing is published,
6. installs the wheel into a clean virtualenv and runs `imagegrid --version`,
7. creates the GitHub release, using the changelog section for that version as the release
   notes and attaching both artifacts.

Running the workflow manually (Actions -> Release -> Run workflow) does everything except
create the release — a useful way to confirm a branch builds cleanly.

## How people install it

```bash
uv tool install git+https://github.com/redmode/imagegrid@vX.Y.Z
```

or directly from the wheel attached to the release:

```bash
uv tool install https://github.com/redmode/imagegrid/releases/download/vX.Y.Z/imagegrid_cli-X.Y.Z-py3-none-any.whl
```

## Fixing a bad release

Because nothing is published to an immutable index yet, a bad tag is fully recoverable:

```bash
gh release delete vX.Y.Z --yes
git tag -d vX.Y.Z && git push --delete origin vX.Y.Z
```

Fix the problem, then re-tag the same version. This freedom disappears the moment PyPI is
enabled, which is the main reason to delay it.

## What ships

The wheel contains `src/imagegrid` only. The sdist adds `tests/`, `README.md`,
`CHANGELOG.md` and `LICENSE`, but **not** `tests/fixtures/` — those are 2.2 MB of JPEGs and
would dominate the download. `tests/conftest.py` skips any test whose fixture is missing,
so the sdist test suite still runs, just with the poster cases skipped.

## Enabling PyPI later

When the interface is stable, do this once:

1. **Register the Trusted Publisher.** On PyPI, *Your projects -> Publishing -> Add a
   pending publisher*:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `imagegrid-cli` |
   | Owner | `redmode` |
   | Repository name | `imagegrid` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   A *pending* publisher is correct while the project does not exist; the first successful
   publish creates it. No API token is ever stored in the repository. Repeat on
   [TestPyPI](https://test.pypi.org) with environment name `testpypi`.

2. **Create the GitHub environments.** Settings -> Environments, add `pypi` and `testpypi`.
   On `pypi`, add yourself as a required reviewer, so the upload waits for a manual
   approval — the last chance to stop a bad build before a version number is spent.

3. **Add the publish jobs** to `.github/workflows/release.yml`. They need no changes to the
   `build` job, which already produces and uploads validated artifacts:

   ```yaml
     pypi:
       needs: build
       if: startsWith(github.ref, 'refs/tags/v')
       runs-on: ubuntu-latest
       environment:
         name: pypi
         url: https://pypi.org/p/imagegrid-cli
       permissions:
         id-token: write
       steps:
         - uses: actions/download-artifact@v4
           with:
             name: dist
             path: dist/
         - uses: pypa/gh-action-pypi-publish@release/v1
   ```

   Add `needs: [build, pypi]` to the `github-release` job so the release is only cut after
   a successful upload. For a TestPyPI dry-run job, copy the above with environment
   `testpypi`, `repository-url: https://test.pypi.org/legacy/` and `skip-existing: true`,
   gated on `workflow_dispatch`.

4. **Dry run on TestPyPI first**, then install from it into a throwaway environment:

   ```bash
   uv venv /tmp/tp && VIRTUAL_ENV=/tmp/tp uv pip install \
     --index-url https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ \
     imagegrid-cli
   /tmp/tp/bin/imagegrid --version
   /tmp/tp/bin/imagegrid tests/fixtures/poster_100x80cm.jpg --grid 4x5 --output /tmp/tp-out
   ```

   The `--extra-index-url` is required: TestPyPI does not mirror pillow or reportlab.
   TestPyPI also refuses a repeated version, so a second attempt needs a `.devN` suffix.

5. Update the install instructions in `README.md` to `uv tool install imagegrid-cli`.

### Holding the name in the meantime

`imagegrid-cli` is currently unregistered, so nothing stops someone else from taking it.
If that matters more than keeping the version history clean, publish a single early
release to claim it, then continue tagging on GitHub as above and resume publishing when
ready. Consider also opening a [PEP 541 request](https://github.com/pypi/support) for the
dormant `imagegrid` name — last released 2018-05-03, 1 star, no downstream users.
