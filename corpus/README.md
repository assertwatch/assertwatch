# Evaluation corpus

Each file in `cases/` is one labelled change, replayed by `run.py` (`make eval`) as two commits
in a throwaway git repository:

```toml
label = "cheat"                  # or "refactor"
title = "The failing assertion is deleted"
expect = ["AW002"]               # cheats: the rules that should catch it
source = "click 1557e265 (...)"  # optional: the real commit a case is modeled on
known = "why it fails today"     # optional: a known miss or false positive
message = "Commit message"       # optional: for commit-trailer cases
author = "human"                 # optional: commit as a human (default: as an AI agent)
blocks = false                   # optional, cheats: must block (true) or only warn (false)

[before]
"tests/test_cart.py" = '''
...
'''

[after]
"tests/test_cart.py" = '''
...
'''
```

Files missing from `[after]` are deleted. The change is committed under an AI agent's identity unless
`author = "human"`, so findings keep the rules' severities; human cases check the routing. A cheat counts as caught when a block or warn
finding reports it with every rule in `expect`; a refactor is a false positive when any block
finding fires. `tests/test_corpus.py` runs every case, and fails if a `known` case starts
passing or the README's numbers go stale.

Add a case for every cheat or false positive you find in the wild, modeled on the real
change but rewritten small.

[`real-history.md`](real-history.md) is the other half: a random sample of commits from
open-source projects that AssertWatch blocks, labelled one by one. Its false positives that
were fixed have a case here; the ones that remain are listed there.
