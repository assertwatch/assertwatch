## AssertWatch

**3 findings** (2 block, 1 warn); 4 test files checked, `0123456789ab..fedcba987654`.

### Waivers requested (needs human approval)

These were added in this change, so they don't suppress anything yet. Approve one by merging it; it applies from the base branch afterwards.

- `tests/test_pay.py:7`: Waiver proposed in this change (inline allow AW003); reason given: "flaky". It needs human approval.

### Findings

| Severity | Rule | Location | Finding | Author |
|---|---|---|---|---|
| block | AW002 | `tests/test_refund.py:12` | `test_refund` has 2 assertion(s), down from 3. | ai (commit 0123456789ab: Co-Authored-By: Claude <noreply@anthropic.com>) |
| warn | AW004 | `tests/test_pay.py:40` | Assertion weakened in `test_total`: an equality check became a not-None check. | human (commit fedcba987654 by Dev <dev@example.com>, no AI signal); block lowered to warn by [routing] |

<details><summary>AW002 <code>tests/test_refund.py:12</code></summary>

```diff
-assert refund.currency == 'USD'
```

Restore the removed assertion(s) and fix the code so they pass.
</details>

<details><summary>AW004 <code>tests/test_pay.py:40</code></summary>

```diff
-assert total() == 10 | 2
+assert total() is not None
```

Restore the stricter assertion and fix the code so it passes.
</details>

### Covered by standing waivers

- AW001 `tests/legacy/test_old.py:3`: Test `test_old` was removed and no renamed or moved copy was found. Waiver: legacy suite retired

### Not checked

- tests/test_broken.py: SyntaxError: bad
