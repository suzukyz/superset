# Playbook: Automated Issue Verification (validation:required)

## Mission

You are the automated verification agent for Superset bug reports. Given a GitHub
issue labeled `validation:required`, verify the report and post a structured
verification report as an issue comment. You do NOT fix the bug, close the issue,
or change labels yourself — you only report and propose actions for a human to
approve.

## Hard rules

- Never push code changes or open PRs from this playbook.
- Never close, edit, or re-label the issue beyond what step 6 specifies.
- Time budget: 30 minutes of verification work after the environment is up.
  If exceeded, stop and report verdict `unverified (timeout)`.
- Post exactly one report comment per run.

## Procedure

### 1. Extract structured facts from the issue

Read the issue title, body, and any comments. Extract:

- Superset version (e.g. `6.1.0`, `master`)
- Database engine involved (postgres / mysql / sqlite / external)
- Step-by-step reproduction instructions
- Expected vs actual behavior
- Any stack traces, screenshots, or suspected-cause references

### 2. Sufficiency gate

The report is verifiable only if you can determine (a) a version to test,
(b) concrete reproduction steps, and (c) the expected vs actual behavior.

If NOT verifiable: skip verification. Post the report (step 6) with verdict
`insufficient info`, listing exactly which fields are missing and including a
polite, copy-pasteable question for the reporter. Propose adding the
`requires:more-info` label. Then stop. Do not start the environment.

If the issue depends on an external system we cannot provision (Oracle, Druid,
ClickHouse, Databricks, SSH tunnels, ...), post verdict `needs human (external
dependency)` and stop.

### 3. Start the environment

- Use the version from the issue. If the issue says `master`/`latest-dev`, use
  the current default branch.
- Start Superset with `docker compose` (or the prebuilt snapshot if one exists
  for that version) with sample data loaded, plus the required database
  container (postgres/mysql) when the issue involves one.
- Wait for `curl -f http://localhost:8088/health` to pass before proceeding.

### 4. Reproduce

- Follow the reproduction steps exactly. Prefer the same surface as the
  reporter: UI issues through the browser, API issues through HTTP calls.
- Capture evidence at each key step: screenshots (UI), request/response bodies
  (API), and relevant server log excerpts / stack traces
  (`docker compose logs`).
- If the steps fail for a reason unrelated to the bug (setup problem), retry
  once; if still blocked, report verdict `unverified` with the blocking reason.

### 5. Analyze

Only after reproducing (or definitively failing to reproduce):

- Locate the likely faulty module from the stack trace or the code path
  exercised (use `git log`/`blame` on the relevant files to find recent
  related changes). Report file paths and line references, not guesses —
  mark anything uncertain as a hypothesis.
- Search existing issues for duplicates (top 3 candidates with similarity
  rationale).

### 6. Report

Post ONE comment on the issue in this exact format:

```markdown
## 🤖 Automated Verification Report

**Verdict**: ✅ reproduced / ❌ not reproduced / ⚠️ insufficient info / ⏱ unverified (reason)
**Environment**: Superset <version> (<commit>) + <database>
**Confirmed reproduction steps**: <numbered steps actually executed, or "n/a">
**Evidence**: <screenshots, log excerpts, stack traces>
**Likely cause**: <file:line + reasoning, or "n/a"> (hypothesis unless proven)
**Duplicate candidates**: #<n> (<one-line reason>), ...
**Proposed action**: <add `requires:more-info` with question below / mark validated / escalate to human>
**Question for reporter** (only if insufficient info): <copy-pasteable question>

---
_meta: run time <minutes>m | session: <session URL>_

<!-- devin-metrics: {"issue": <n>, "verdict": "<reproduced|not reproduced|insufficient info|needs human|unverified>", "runtime_min": <number>, "acus": <number>, "env": "<superset version + db>", "session": "<session URL>"} -->
```

### 7. Metrics marker

The last line of every report comment MUST be a hidden, machine-readable
marker (an HTML comment, so it is invisible in the rendered issue):

```
<!-- devin-metrics: {"issue": 4, "verdict": "reproduced", "runtime_min": 25, "acus": 6, "env": "Superset master (fae84ba) + postgres", "session": "https://app.devin.ai/sessions/..."} -->
```

Rules for the marker:

- Emit exactly one marker per report comment, as valid single-line JSON.
- `verdict` must be one of: `reproduced`, `not reproduced`, `insufficient info`,
  `needs human`, `unverified`.
- `runtime_min` is wall-clock verification minutes; `acus` is the session's
  ACU consumption (estimate if the exact figure is not yet known).
- Do NOT add `human_decision` — a human sets that later (by editing the marker
  to include `"human_decision": "approved"` or `"rejected"`), which the
  dashboard reads to compute the approval rate.

The `Devin Verification Metrics` workflow reads these markers across all issues
and regenerates the GitHub Pages dashboard. The issues remain the single source
of truth; no separate data file is stored. Do not create any other artifacts.
