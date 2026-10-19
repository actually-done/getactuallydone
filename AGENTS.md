# Critical rules for this repository

- Never run `git push` yourself, in any form (`git -C . push`, `sh -c "git push"`, `env X=1 git push`, etc.) - a hook blocks it, but don't try. Ask the human to push.
- "Done" means the `actually-done` full-tier CI check passes, not that your own tests pass locally. It runs base's test files and config against your code - editing an existing test's assertion, or adding a skip/xfail marker to dodge a failure, changes nothing.
- Never edit or delete an existing test to make it pass. If you believe a test is wrong, stop and say why instead of changing it.
- Adding a new test is fine and encouraged. Don't mark a new test skip/xfail - that fails the check too.
- Never create or edit `conftest.py`, `pytest.ini`, `setup.cfg`, `.verify/`, `CODEOWNERS`, or anything under `.github/` without being asked - these are grader paths and need human review.
- If a test needs to be removed or approved as a known failure, ask the human to run `actually-done approve <test-id>` - you can't do this yourself.
