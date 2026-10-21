"""Full tier: the decisive, CI-only check. Base's test files decide what has to pass -
the PR can add new tests or change production code, but can never weaken an existing
test, sneak in a new conftest.py, or quietly drop tests without an approved removal.

The approach: build a sandbox that's the PR's full source tree, but with every test
file that exists in both trees, every conftest.py/pytest.ini/setup.cfg, and
tests/oracle/ replaced by base's content (denying any such grader-path file the PR
introduces that base doesn't have at all). A test file the PR deletes stays deleted -
the test-count check, not a silent resurrection, is what catches an unapproved
removal. Run pytest there.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from actually_done.config import Config

_SKIP_MARKER_RE = re.compile(r"@pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail)\s*\(")
_GRADER_CONFIG_NAMES = {"conftest.py", "pytest.ini", "setup.cfg"}
_SANDBOX_EXCLUDES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".actually-done",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}
_COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected")
_TAIL_LINES = 80


@dataclass(frozen=True)
class FullResult:
    status: str  # "pass" | "fail" | "error"
    summary: str
    details: str = ""


@dataclass(frozen=True)
class JunitReport:
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    failing: tuple[tuple[str, str], ...] = ()  # (test id, first line of its message)


@dataclass
class Evidence:
    base_ref: str
    pr_commit: str
    commands: list[tuple[str, int]] = field(default_factory=list)
    new_test_files: int = 0
    tests_collected: int = 0
    failing_tests: tuple[tuple[str, str], ...] = ()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return hash_bytes(path.read_bytes())


@dataclass(frozen=True)
class Approvals:
    """Parsed, validated `.verify/approved`, from `_parse_approved` - see there
    for the format and the reasoning behind each category.
    """

    deselected: frozenset[str] = frozenset()
    accepted_edit_files: frozenset[str] = frozenset()
    mismatched: tuple[str, ...] = ()
    legacy: tuple[str, ...] = ()


def _parse_approved(base_root: Path, pr_root: Path) -> Approvals:
    """Each `.verify/approved` line is one of:

      `<test id> <base sha256>`                       - approved for removal/skip
      `<test id> <base sha256> <approved new sha256>`  - approved EDIT

    Both forms are bound to base's content at approval time (`actually-done
    approve` / `--to <ref>`, cli.py's `_cmd_approve`) and re-verified against
    base's CURRENT content here - a base file that no longer matches its
    recorded hash (edited further, or deleted and recreated) voids that entry
    outright: not "approved with a stale binding," simply not approved, so the
    test runs for real again. This closes a real gap: matching on the id
    string alone would let one approval, once merged, cover ANY future test
    that happens to share that exact id - including a file deleted and later
    recreated at the same path (a new file, so common_tests' "base's content
    always wins" lock doesn't apply to it at all), or a stale entry whose
    original file is long gone quietly discounting the required test count for
    an unrelated deletion forever.

    The two-field form means exactly what it did before: `deselected` test ids
    are excluded from the run entirely (D18).

    The three-field form approves a SPECIFIC edit - not just "don't run this,"
    but "accept exactly this new content as a replacement for base's, and run
    it for real." `common_tests`' lock (base's content always wins for a file
    that exists on both sides) has one narrow exception for these:
    `accepted_edit_files` names the ones where the PR's CURRENT content
    matches the approved new hash exactly, so `run_full_tier` lets the PR's
    version through instead of overwriting it with base's. If the PR's content
    matches NEITHER the base hash (untouched) NOR the approved new hash (the
    approved edit), that's a PR that changed an approved-edit test to
    something nobody signed off on - `mismatched` surfaces it so
    `run_full_tier` can fail loudly, not silently fall back to base's content
    as if nothing were wrong.

    A line that isn't one of the two valid shapes at all - almost always a
    leftover from before approvals were bound to content - is neither silently
    honored NOR silently dropped: `legacy` surfaces it the same way, so
    `run_full_tier` fails loudly rather than letting a human discover later
    that an approval they thought was in effect never actually was.
    """
    path = base_root / ".verify" / "approved"
    if not path.is_file():
        return Approvals()

    deselected: set[str] = set()
    accepted_edit_files: set[str] = set()
    mismatched: list[str] = []
    legacy: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        parts = entry.split()
        if len(parts) not in (2, 3):
            legacy.append(entry)
            continue

        test_id, base_hash = parts[0], parts[1]
        file_rel = test_id.split("::", 1)[0]
        base_file = base_root / file_rel
        if not (base_file.is_file() and file_hash(base_file) == base_hash):
            continue  # stale - base has moved past what was approved; void

        if len(parts) == 2:
            deselected.add(test_id)
            continue

        new_hash = parts[2]
        pr_file = pr_root / file_rel
        pr_hash = file_hash(pr_file) if pr_file.is_file() else None
        if pr_hash == new_hash:
            accepted_edit_files.add(file_rel)
        elif pr_hash is not None and pr_hash != base_hash:
            mismatched.append(test_id)
        # else: the PR hasn't touched the file (still == base_hash) or deleted
        # it - no-op, ordinary locking/count-drop handling applies as usual.

    return Approvals(
        deselected=frozenset(deselected),
        accepted_edit_files=frozenset(accepted_edit_files),
        mismatched=tuple(mismatched),
        legacy=tuple(legacy),
    )


def read_approved(base_root: Path, pr_root: Path) -> set[str]:
    """Test ids approved for removal/skip - see `_parse_approved`."""
    return set(_parse_approved(base_root, pr_root).deselected)


def read_approval_errors(base_root: Path, pr_root: Path) -> list[str]:
    """Legacy/malformed `.verify/approved` entries - see `_parse_approved`."""
    return list(_parse_approved(base_root, pr_root).legacy)


@contextmanager
def materialize_base(pr_root: Path, base: str) -> Iterator[Path]:
    """Yield a directory holding `base`'s files: used as-is if it names an existing
    directory, otherwise checked out via a detached git worktree and cleaned up after."""
    candidate = Path(base)
    if candidate.is_dir():
        yield candidate
        return

    with tempfile.TemporaryDirectory(prefix="actually-done-base-") as tmp:
        worktree = Path(tmp) / "base"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), base],
            cwd=pr_root,
            check=True,
            capture_output=True,
        )
        try:
            yield worktree
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=pr_root,
                check=False,
                capture_output=True,
            )


def run_full_tier(
    pr_root: Path,
    base_root: Path,
    config: Config,
    base_ref: str = "",
    python: str | None = None,
) -> tuple[FullResult, Evidence]:
    """`python`: the interpreter that actually runs the PR's test suite - defaults
    to `sys.executable` (this process's own), correct for local/single-environment
    use. Pass an explicit path when the grader itself runs isolated from the PR's
    installed dependencies (CI: the grader is installed from base into its own
    venv and invoked by absolute path specifically so a PR can't shadow it with a
    same-named package, a `.pth` file, or a `sitecustomize.py` sharing its
    environment - see docs/DECISIONS.md, D19/D20 - and that isolated grader needs
    a *different* interpreter, one with the PR's own dependencies installed, to
    actually run the PR's tests).
    """
    evidence = Evidence(base_ref=base_ref or str(base_root), pr_commit=_rev_parse(pr_root))

    approvals = _parse_approved(base_root, pr_root)
    if approvals.legacy:
        result = FullResult(
            status="error",
            summary=(
                f"{len(approvals.legacy)} legacy approval format entr"
                f"{'y' if len(approvals.legacy) == 1 else 'ies'} in .verify/approved "
                "- re-approve with `actually-done approve <id>` on base"
            ),
            details="\n".join(approvals.legacy),
        )
        return result, evidence

    if approvals.mismatched:
        result = FullResult(
            status="fail",
            summary=(
                f"{len(approvals.mismatched)} test(s) have an approved-edit entry "
                "in .verify/approved whose content no longer matches the PR - "
                "re-approve with `actually-done approve --to <ref> <id>` on base"
            ),
            details="\n".join(approvals.mismatched),
        )
        return result, evidence

    approved = set(approvals.deselected)

    base_tests = _find_test_files(base_root, config.unit_test_path)
    pr_tests = _find_test_files(pr_root, config.unit_test_path)
    new_tests = pr_tests - base_tests
    common_tests = (base_tests & pr_tests) - approvals.accepted_edit_files
    evidence.new_test_files = len(new_tests)

    skip_violations = sorted(
        rel
        for rel in new_tests
        if _SKIP_MARKER_RE.search((pr_root / rel).read_text(encoding="utf-8", errors="replace"))
    )
    if skip_violations:
        result = FullResult(
            status="fail",
            summary=f"{len(skip_violations)} newly added test file(s) carry a skip/xfail marker",
            details="\n".join(skip_violations),
        )
        return result, evidence

    with tempfile.TemporaryDirectory(prefix="actually-done-sandbox-") as tmp:
        sandbox = Path(tmp) / "sandbox"
        _build_sandbox(sandbox, pr_root, base_root, config, common_tests)

        baseline_count = _collect_count(base_root, config.unit_test_path, evidence, python)

        junit_path = Path(tmp) / "junit-unit.xml"
        returncode, output = _run_pytest(
            sandbox, config.unit_test_path, junit_path, evidence, deselect=approved, python=python
        )
        report = _parse_junit(junit_path)
        evidence.tests_collected = report.tests
        evidence.failing_tests = report.failing

        mismatch = _cross_check(returncode, report)
        if mismatch:
            result = FullResult(status="error", summary=mismatch, details=_tail(output))
            return result, evidence

        expected_minimum = baseline_count - len(approved)
        if report.tests < expected_minimum:
            result = FullResult(
                status="fail",
                summary=(
                    f"test count dropped: expected at least {expected_minimum}, "
                    f"got {report.tests}"
                ),
                details=_tail(output),
            )
            return result, evidence

        if report.failures > 0 or report.errors > 0:
            result = FullResult(
                status="fail",
                summary=f"{report.failures + report.errors} unit test(s) failed",
                details=_tail(output),
            )
            return result, evidence

        oracle_dir = base_root / "tests" / "oracle"
        if oracle_dir.is_dir() and any(oracle_dir.rglob("test_*.py")):
            oracle_junit = Path(tmp) / "junit-oracle.xml"
            oracle_rc, oracle_output = _run_pytest(
                sandbox,
                "tests/oracle",
                oracle_junit,
                evidence,
                extra_args=["--runxfail"],
                python=python,
            )
            if oracle_rc != 0:
                evidence.failing_tests = _parse_junit(oracle_junit).failing
                result = FullResult(
                    status="fail", summary="oracle test(s) failed", details=_tail(oracle_output)
                )
                return result, evidence

    # A run that collects zero tests looks identical to "nothing changed, all
    # good" unless it's checked separately - and base finding zero at the
    # configured unit_test_path too (not just an approved removal bringing the
    # count down to zero, which baseline_count > 0 rules out below) means the
    # path is wrong, misconfigured, or a PR repointed its own config to blind
    # this check - not that there's genuinely nothing to verify.
    if report.tests == 0 and baseline_count == 0:
        result = FullResult(
            status="fail",
            summary=(
                f"zero tests were collected at {config.unit_test_path!r} on both base and "
                "the PR - nothing was verified"
            ),
            details=_tail(output),
        )
        return result, evidence

    result = FullResult(status="pass", summary=f"{report.tests} test(s) passed")
    return result, evidence


def render_evidence_markdown(evidence: Evidence, result: FullResult) -> str:
    lines = [
        "### actually-done full-tier evidence",
        "",
        f"- Base: `{evidence.base_ref}`",
        f"- PR commit: `{evidence.pr_commit}`",
        f"- New test files: {evidence.new_test_files}",
        f"- Tests collected: {evidence.tests_collected}",
        f"- Result: **{result.status}** - {result.summary}",
        "",
        "| Command | Exit code |",
        "| --- | --- |",
    ]
    lines += [f"| `{cmd}` | {code} |" for cmd, code in evidence.commands]
    if evidence.failing_tests:
        lines += ["", "**Failing tests:**", ""]
        lines += [f"- `{test_id}` - {first_line}" for test_id, first_line in evidence.failing_tests]
    if result.details:
        lines += [
            "",
            "<details><summary>Details</summary>",
            "",
            "```",
            result.details,
            "```",
            "</details>",
        ]
    return "\n".join(lines)


def _rev_parse(repo_root: Path, ref: str = "HEAD") -> str:
    completed = subprocess.run(
        ["git", "rev-parse", ref], cwd=repo_root, capture_output=True, text=True
    )
    return completed.stdout.strip() if completed.returncode == 0 else ref


def _find_test_files(root: Path, unit_test_path: str) -> set[str]:
    base_dir = root / unit_test_path
    if not base_dir.is_dir():
        return set()
    return {
        p.relative_to(root).as_posix()
        for p in base_dir.rglob("*.py")
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    }


def _is_excluded(path: Path, root: Path) -> bool:
    return any(part in _SANDBOX_EXCLUDES for part in path.relative_to(root).parts)


def _ignore_excludes(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in _SANDBOX_EXCLUDES}


def _build_sandbox(
    sandbox: Path, pr_root: Path, base_root: Path, config: Config, common_tests: set[str]
) -> None:
    """The PR's full tree, except: every test file that exists in BOTH trees gets
    base's content (so editing one changes nothing), and grader-config files are
    locked the same way. A test file the PR deleted stays deleted here - leaving
    that for the caller's test-count check to catch, rather than silently
    resurrecting it, which would make that check meaningless.
    """
    shutil.copytree(pr_root, sandbox, ignore=_ignore_excludes)

    for rel in common_tests:
        dst = sandbox / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_root / rel, dst)

    _lock_grader_configs(sandbox, pr_root, base_root)

    oracle_dst = sandbox / "tests" / "oracle"
    if oracle_dst.exists():
        shutil.rmtree(oracle_dst)
    oracle_src = base_root / "tests" / "oracle"
    if oracle_src.is_dir():
        shutil.copytree(oracle_src, oracle_dst, ignore=_ignore_excludes)


def _lock_grader_configs(sandbox: Path, pr_root: Path, base_root: Path) -> None:
    """conftest.py / pytest.ini / setup.cfg: base's content wins everywhere it has one;
    anything the PR introduces at a path base doesn't have at all is denied outright."""
    pr_paths = {
        p.relative_to(pr_root).as_posix()
        for p in pr_root.rglob("*")
        if p.name in _GRADER_CONFIG_NAMES and not _is_excluded(p, pr_root)
    }
    base_paths = {
        p.relative_to(base_root).as_posix()
        for p in base_root.rglob("*")
        if p.name in _GRADER_CONFIG_NAMES and not _is_excluded(p, base_root)
    }

    for rel in base_paths:
        dst = sandbox / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base_root / rel, dst)

    for rel in pr_paths - base_paths:
        dst = sandbox / rel
        if dst.is_file():
            dst.unlink()


def _pytest_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def _collect_count(
    cwd: Path, unit_test_path: str, evidence: Evidence, python: str | None = None
) -> int:
    """Collect-only test count, run with `cwd` set so relative imports (`from pkg
    import ...`) resolve against that tree's own production code - not whatever
    directory the calling process happened to be in."""
    if not (cwd / unit_test_path).is_dir():
        return 0
    command = [
        python or sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        unit_test_path,
        "--override-ini=addopts=",
        "-p",
        "no:cacheprovider",
    ]
    completed = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, env=_pytest_env()
    )
    evidence.commands.append((" ".join(command), completed.returncode))
    match = _COLLECTED_RE.search(completed.stdout)
    return int(match.group(1)) if match else 0


def _run_pytest(
    sandbox: Path,
    test_path: str,
    junit_path: Path,
    evidence: Evidence,
    deselect: set[str] | None = None,
    extra_args: list[str] | None = None,
    python: str | None = None,
) -> tuple[int, str]:
    command = [
        python or sys.executable,
        "-m",
        "pytest",
        "-q",
        test_path,
        "--override-ini=addopts=",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit_path}",
    ]
    for test_id in sorted(deselect or ()):
        command += ["--deselect", test_id]
    if extra_args:
        command += extra_args

    completed = subprocess.run(
        command, cwd=sandbox, capture_output=True, text=True, env=_pytest_env()
    )
    evidence.commands.append((" ".join(command), completed.returncode))
    return completed.returncode, completed.stdout + completed.stderr


def _parse_junit(path: Path) -> JunitReport:
    if not path.is_file():
        return JunitReport()
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return JunitReport()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return JunitReport()
    return JunitReport(
        tests=int(suite.get("tests", 0)),
        failures=int(suite.get("failures", 0)),
        errors=int(suite.get("errors", 0)),
        skipped=int(suite.get("skipped", 0)),
        failing=tuple(_junit_failures(suite)),
    )


def _junit_failures(suite: ET.Element) -> list[tuple[str, str]]:
    """Every <testcase> with a <failure>/<error> child: its test id and the first
    non-blank line of that child's message - so a human reading the evidence
    comment sees exactly what broke without having to dig through raw pytest
    output (which full.py's own evidence only ever keeps the last 80 lines of).
    """
    failing: list[tuple[str, str]] = []
    for case in suite.findall("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is None:
            continue
        message = problem.get("message") or problem.text or ""
        failing.append((_junit_test_id(case), _first_nonblank_line(message)))
    return failing


def _junit_test_id(case: ET.Element) -> str:
    name = case.get("name", "")
    file = case.get("file")
    if file:
        return f"{file}::{name}"
    classname = case.get("classname", "")
    return f"{classname}::{name}" if classname else name


def _first_nonblank_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "(no message)"


def _cross_check(returncode: int, report: JunitReport) -> str | None:
    broken = report.failures > 0 or report.errors > 0
    if returncode == 0 and broken:
        return "pytest exited 0 but the JUnit report shows failures/errors - treating as broken"
    if returncode == 5 and report.tests == 0:
        return None  # "no tests collected" - consistent; the count check decides if that's OK
    if returncode not in (0, 1):
        return f"pytest exited with code {returncode} (not a normal pass/fail)"
    return None


def _tail(output: str) -> str:
    return "\n".join(output.splitlines()[-_TAIL_LINES:])
