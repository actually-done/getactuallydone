"""Attack 9: a PR's own install - sharing ONE Python environment with the grader -
could replace actually-done outright: a `.pth` file dropped into that shared
environment's site-packages can override what `import actually_done` resolves to
for anything running there, including the grader itself. No package-name
collision is needed at all (that's a narrower, separate problem - see
docs/DECISIONS.md, D19) - a `.pth` file's own lines starting with `import` run
arbitrary code at interpreter startup, a documented feature of Python's `site`
module, not a bug being exploited here.

Caught by: installing the grader from base into its OWN venv (never touched by
the PR's install), invoked afterward by absolute path, with the PR's own
dependencies going into a completely separate venv - actually-done.yml and the
shipped template (src/actually_done/templates/workflow.yml) both do this since
D20. The first test below proves the attack is real against the OLD, unisolated,
single-shared-venv design; the second proves the isolated design closes it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import venv
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _venv_python(venv_dir: Path) -> Path:
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    exe = "python.exe" if sys.platform == "win32" else "python"
    return venv_dir / bin_dir / exe


def _venv_site_packages(python: Path) -> Path:
    # site.getsitepackages()[0] is the venv's ROOT on Windows, not its real
    # site-packages dir (that's a later entry) - pick the one actually named
    # "site-packages" instead of assuming a fixed index.
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import json, site; print(json.dumps(site.getsitepackages()))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    candidates = [Path(p) for p in json.loads(completed.stdout)]
    for candidate in candidates:
        if candidate.name == "site-packages":
            return candidate
    return candidates[0]


def _plant_shadow_pth(venv_dir: Path) -> None:
    """A malicious PR's install planting a `.pth` file is the general form of
    this attack - it needs no package-name collision with the grader at all,
    which is why this applies to any project using actually-done, not just a
    repo (like this one) whose own package happens to be named actually-done.

    CPython's site.py only exec's a `.pth` line that *starts with* `import` -
    each qualifying line is exec'd independently, not as part of a multi-line
    script, so everything has to live on that one line (semicolon-joined).
    """
    python = _venv_python(venv_dir)
    site_packages = _venv_site_packages(python)
    (site_packages / "zzz_shadow.pth").write_text(
        "import sys, types; m = types.ModuleType('actually_done'); m.EVIL = True; "
        "sys.modules['actually_done'] = m\n",
        encoding="utf-8",
    )


def _install_actually_done(python: Path) -> None:
    subprocess.run(
        [str(python), "-m", "pip", "install", "-e", str(_REPO_ROOT), "-q"],
        check=True,
        capture_output=True,
    )


def _import_evil_flag(python: Path) -> str:
    completed = subprocess.run(
        [str(python), "-c", "import actually_done; print(getattr(actually_done, 'EVIL', False))"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def test_shadow_package_in_a_shared_venv_replaces_the_grader(tmp_path: Path) -> None:
    """Proof this is a real attack, not a hypothetical one: a single shared venv
    (the unisolated design this repo's own workflow used before D20) lets a
    PR-planted .pth file replace `import actually_done` outright.
    """
    shared_venv = tmp_path / "shared-venv"
    venv.create(shared_venv, with_pip=True)
    python = _venv_python(shared_venv)
    _install_actually_done(python)

    _plant_shadow_pth(shared_venv)  # what a malicious PR's own install could do

    assert _import_evil_flag(python) == "True"


def test_isolated_grader_venv_is_unaffected_by_a_shadow_in_the_pr_venv(tmp_path: Path) -> None:
    """The fix: a separate venv for the grader, never touched by the PR's own
    install - the same shadow attack, planted in the PR's venv, has no effect on
    what the grader venv's own `import actually_done` resolves to.
    """
    grader_venv = tmp_path / "grader-venv"
    pr_venv = tmp_path / "pr-venv"
    venv.create(grader_venv, with_pip=True)
    venv.create(pr_venv, with_pip=True)

    grader_python = _venv_python(grader_venv)
    pr_python = _venv_python(pr_venv)
    _install_actually_done(grader_python)
    _install_actually_done(pr_python)

    _plant_shadow_pth(pr_venv)  # the "PR" venv is compromised

    assert _import_evil_flag(pr_python) == "True"  # confirms the shadow really landed there
    assert _import_evil_flag(grader_python) == "False"  # the grader's own venv is untouched
