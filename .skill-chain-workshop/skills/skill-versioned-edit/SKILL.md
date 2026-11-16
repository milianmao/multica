---
name: skill-versioned-edit
description: Use when modifying, upgrading, optimizing, refactoring, or rewriting any project SKILL.md or SKILL chain. Before any edit, create a rerunnable pure backup with frozen suite/cases/mutations and no evaluation; only after the user explicitly says all edits are complete, evaluate the frozen before version and current after version and strictly assess enhanced, unchanged, mixed, or regressed.
evaluated_by:
  - SVE-E-001
  - SVE-M-001
---

# Versioned SKILL Edit

Protect every project SKILL change with a deferred A/B workflow.

## Non-negotiable rules

1. **Before writing any target `SKILL.md`, run Prepare successfully.**
2. Prepare is backup-only. It MUST call `benchmark/run.py backup`; it MUST NOT run `skills`, `mutation`, `snapshot`, `assess`, or `full`.
3. One Change ID covers all declared targets in the same change batch.
4. Do not evaluate until the user explicitly confirms that all edits are complete or explicitly requests A/B evaluation.
5. Evaluate both the frozen Before source and current After source with the frozen Suite, Cases, and Mutation definitions.
6. Do not claim capability improvement without a completed strict assessment.
7. Never overwrite an existing Change ID, backup, or completed record.

## Phase 1 — Prepare only

Run before editing:

```bash
python skills/skill-versioned-edit/scripts/prepare_change.py \
  --target skills/example-a/SKILL.md \
  --target skills/example-b/SKILL.md
```

Optional explicit ID:

```bash
python skills/skill-versioned-edit/scripts/prepare_change.py \
  --change-id my-skill-upgrade-v2 \
  --target skills/example-a/SKILL.md
```

Prepare must finish with:

```text
Backup integrity: PASS
Evaluation executed: NO
SAFE TO EDIT
```

It records the immutable Before backup and frozen evaluation inputs under `benchmark/`, as provided by the benchmark `backup` API. If backup integrity fails, stop without editing.

## Phase 2 — Edit

After `SAFE TO EDIT`, modify all targets associated with the Change ID. Do not make a new backup for every individual edit. Do not modify the frozen backup or its Suite/Case/Mutation payload.

If the scope gains another target, prepare a new Change ID before editing that undeclared target.

## Phase 3 — Explicit evaluation

Only after the user confirms the batch is complete, run:

```bash
python skills/skill-versioned-edit/scripts/evaluate_change.py \
  --change-id my-skill-upgrade-v2

# Only after a failed/interrupted attempt:
python skills/skill-versioned-edit/scripts/evaluate_change.py \
  --change-id my-skill-upgrade-v2 --retry
```

Evaluate performs:

1. Integrity checks for the frozen Suite and Mutation files.
2. Mutation evaluation of the frozen Before subject.
3. Mutation evaluation of current After.
4. Normalization of each standalone mutation result:

   ```python
   result = mutation["baseline"]
   result["mutation"] = mutation
   ```

5. Strict `benchmark/run.py assess` with no Suite drift.
6. Record update with result paths and `enhanced`, `unchanged`, `mixed`, or `regressed`.

A non-zero mutation gate is valid comparison evidence when its `mutations.json` exists. A missing result is an execution failure. A `regressed` verdict returns exit code 1; workflow/input errors return exit code 2.

## Reporting

Always report:

- Change ID and declared targets;
- Before backup path;
- frozen Suite and Mutation hashes;
- whether evaluation was deferred or completed;
- Before and After result paths;
- strict verdict;
- Critical, Hard/Adversarial, and Mutation regressions from the assessment.

Static benchmark evidence validates documented SKILL-chain contracts and mutation sensitivity; it does not by itself prove real-model end-to-end execution success.
