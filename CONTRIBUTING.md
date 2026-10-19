# Contributing

## Setup

```bash
pip install -e ".[dev]"
```

## Before opening a PR

```bash
pytest
ruff check .
mypy src
```

All three must pass. If you touch a hook script (`plugin/scripts/*.sh`), also run
`scripts/clean_install_check.sh` - it builds the wheel and smoke-tests a clean install,
which `pytest` alone doesn't cover.

## Ground rules

- **Never weaken or delete a test to make it pass.** If a test looks wrong, say why in
  the PR instead of changing it.
- **Grader paths need extra care.** `conftest.py`, `pytest.ini`, `setup.cfg`,
  `.github/`, `.verify/`, and this project's own hooks/rules files exist specifically
  so an agent can't quietly weaken them - see `solution.md` §3 and `docs/DECISIONS.md`.
  Changes here get closer review.
- **A bug fix or a new attack the suite doesn't catch yet is always welcome.** Add it to
  `tests/attacks/` (or the relevant file in `tests/unit/`) with a docstring explaining
  what it demonstrates and why the existing defense does or doesn't catch it.
- **Keep PRs scoped.** One commit per logical change is easier to review than one large
  diff - see this repo's own commit history for the expected granularity.

## Releasing

`.github/workflows/release.yml` runs on any tag matching `v*`: it re-runs the full test
suite, checks the tag matches `pyproject.toml`'s `version` (and, separately,
`test_version_consistency.py` keeps `pyproject.toml` and `plugin.json` in sync with each
other), builds the sdist/wheel, and publishes to PyPI via
[trusted publishing](https://docs.pypi.org/trusted-publishers/) - no API token is
stored in this repo.

One-time setup this workflow depends on, done on PyPI's side, not here:

1. On PyPI, add a trusted publisher for this project: GitHub owner/repo
   `actually-done/actually-done` (or wherever this ends up), workflow filename
   `release.yml`, environment name `pypi`.
2. Optionally, under this repo's Settings → Environments, create a `pypi` environment
   and add required reviewers for a manual approval gate before every publish. The
   workflow references this environment either way; it's a no-op if left unconfigured.

To cut a release: bump the version in `pyproject.toml` and `plugin.json` together (the
version-consistency test fails the PR otherwise), merge, then tag and push:

```bash
git tag v0.1.0
git push origin v0.1.0
```

## Design background

`solution.md` is the design source. `docs/DECISIONS.md` records every place the
implementation differs from or refines it, based on the current Claude Code docs -
read it before changing hook behavior. `ROADMAP.md` lists what's deliberately out of
scope for now.
