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

## Design background

`solution.md` is the design source. `docs/DECISIONS.md` records every place the
implementation differs from or refines it, based on the current Claude Code docs -
read it before changing hook behavior. `ROADMAP.md` lists what's deliberately out of
scope for now.
