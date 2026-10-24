# Mutation Testing Harnesses

[🇺🇸 English Version](README.en.md) | [🇧🇷 Versão em Português](LEIAME.md)

Each harness in this directory **deliberately introduces defects** into a specific component of the production codebase, executes the corresponding test suite, and requires it to **fail**. If the test suite remains green despite the reintroduced bug, the test fails to catch the regression — and the mutation harness flags it as a surviving mutant.

A passing test suite proves the code passes. Only a mutation harness proves the test suite would **reliably catch the bug if reintroduced**.

---

## Running Mutation Tests

From the repository root:

```bash
.venv/bin/python tests/mutacao/mutate_identity.py
.venv/bin/python tests/mutacao/mutate_file_watcher.py
```

Output: one status line per mutation and an aggregated summary. **Exit code 0** occurs only when there are zero survivors and zero invalid mutations, making it suitable for automated regression checks.

---

## Mutation Status Categories

| Category | Definition | Action Required |
|---|---|---|
| **KILLED** | The test suite failed when the defect was injected. Expected outcome. | None |
| **SURVIVED** | The test suite passed despite the defect. **Genuine test coverage gap.** | Write tests targeting the gap |
| **EQUIVALENT** | The mutation does not alter observable behavior. Not a gap. | **Measure** and declare in `EQUIVALENTES` |
| **INVALID** | The code snippet did not match; mutation was not applied. | Fix the anchor string in the harness |

**Equivalent mutations must be measured, not assumed:** A surviving mutant may appear to be a gap when it is actually an equivalent transformation. Verify observable differences before altering existing tests.

**Invalid mutations are the most deceptive failure mode:** A test runner might report that a test "passed", but the mutation was never applied in the first place. The harness checks whether the anchor string exists before mutating.

---

## Anchor String Uniqueness Requirements

The harness applies `original.replace(source, replacement, 1)` — replacing the **first** occurrence. If an anchor string matches more than once, it mutates the first occurrence while attributing the result to the described mutation, reporting inaccurate KILLED/SURVIVED metrics.

All anchor strings must be strictly unique within the target file.

