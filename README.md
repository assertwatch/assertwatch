# AssertWatch

> Your agent says the tests pass. AssertWatch checks the tests weren't weakened to make that true.

**Status: pre-alpha. Nothing works yet.** Building in public.

AssertWatch reads a git diff and flags test weakening by AI coding agents: deleted tests, dropped
or loosened assertions, new skips, rewritten expected values, mocks of the code under test, and
relaxed test or coverage config. Deterministic, no LLM, runs offline. Python (pytest) first, then
Java (JUnit 5).

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q
assertwatch --version
```

With [uv](https://docs.astral.sh/uv/): `uvx --from . assertwatch --version`.

## License

MIT
