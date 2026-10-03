# Security policy

AssertWatch runs in CI and inside coding-agent sessions, so a bug that lets a change slip a
weakened test past it, or makes it run something it shouldn't, counts as a security issue.

## Reporting a vulnerability

Please report privately through GitHub: **Security → Report a vulnerability** on this
repository. Don't open a public issue.

Include the version (`assertwatch --version`), what you ran, and a minimal diff or repository
that shows the problem. You should hear back within 7 days.

## In scope

- Code execution or file writes outside the repository while reading a diff or config.
- A way for a change to switch off or relax its own check: config, waivers or the Action's
  base taken from the change instead of the base branch.
- The GitHub Action leaking tokens or running code from an untrusted pull request.

A test-weakening pattern AssertWatch doesn't detect yet is a false negative, not a
vulnerability. Please open a regular issue with the diff; it goes into the corpus.

## Supported versions

Only the latest release gets fixes while the project is pre-1.0.
