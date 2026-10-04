# Contributing

Thanks for helping. The most useful contributions are diffs:

- **A missed cheat:** a change that weakens a test and passes AssertWatch. Open an issue with
  the smallest diff that shows it.
- **A false positive:** a legitimate refactor that AssertWatch blocks. Same thing: the diff,
  plus a line on why the change is fine.

Both become cases in `corpus/`, which is how `make eval` measures recall and false positives.

## Setup

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
make check     # ruff, format check, tests
make eval      # recall and false positives on the corpus
make history   # replay 1,318 real commits (clones six projects once, ~10 min)
```

## Pull requests

- Keep each PR to one change, with tests. A new or changed rule needs corpus cases: at least
  one cheat it catches and one refactor it stays quiet on.
- If `make eval` numbers move, update the README (a test checks they match). A rule change
  also reruns `make history`; put the before/after blocking count in the PR.
- The check is deterministic and offline: no network calls and no LLM in the analysis path.
- AssertWatch runs on its own PRs. If your change removes or weakens a test on purpose, say
  why in the PR description.

Contributions are accepted under the [Apache-2.0](LICENSE) license.
