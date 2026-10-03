#!/bin/sh
# Builds the repository recorded in demo.tape: a commit with honest tests, then the kind of
# uncommitted change an agent makes to get a failing test to pass.
set -eu
dir=$1
rm -rf "$dir" && mkdir -p "$dir" && cd "$dir"
git init -q -b main
git config user.name demo && git config user.email demo@example.com
cat > test_prices.py <<'PY'
import pytest

from prices import total


def test_total_adds_tax():
    assert total(10, 0.05) == 10.5
    assert total(0, 0.05) == 0


def test_total_rejects_negative():
    with pytest.raises(ValueError):
        total(-1, 0.05)
PY
cat > prices.py <<'PY'
def total(price, tax):
    if price < 0:
        raise ValueError("negative price")
    return round(price * (1 + tax), 2)
PY
git add . && git commit -qm "add prices"
cat > test_prices.py <<'PY'
import pytest

from prices import total


def test_total_adds_tax():
    assert total(10, 0.05) is not None


@pytest.mark.skip(reason="flaky")
def test_total_rejects_negative():
    with pytest.raises(ValueError):
        total(-1, 0.05)
PY
