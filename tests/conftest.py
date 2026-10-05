import pytest

# Commits in the tests are made by an agent account, so routing (attribution.py) keeps their
# findings at the rules' severities. Tests of human-authored changes set their own identity.
AGENT = ("test-agent[bot]", "test-agent[bot]@example.invalid")


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch, tmp_path_factory):
    """Keep the user's git config (signing, hooks, identity) out of the tests."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{var}_NAME", AGENT[0])
        monkeypatch.setenv(f"GIT_{var}_EMAIL", AGENT[1])
