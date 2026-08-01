# Devin Automated Issue Verification

An automation solution that removes the biggest bottleneck in Superset's issue
triage: the **human "reproduce-and-verify" wait**. When a new bug report is
labeled `validation:required`, Devin reads the issue, spins up a Superset
environment **in Docker**, attempts to reproduce the bug, and posts a structured
**verification report** back onto the issue. A human then only has to
**approve or reject** that report instead of building an environment and
reproducing the bug by hand.

The solution also publishes a **GitHub Pages dashboard** that reports on itself
(reproduce rate, human approval rate, runtime, ACU usage, estimated cost) so its
own accuracy and value can be measured over time.

> This lives entirely under `.devin/`, `.github/workflows/`, and
> `scripts/devin_verification_metrics/`. It does **not** modify Superset's
> application code, build, or documentation site.

---

## Table of contents

- [Why](#why)
- [Architecture](#architecture)
- [Components](#components)
- [End-to-end flow](#end-to-end-flow)
- [Labels](#labels)
- [Running / simulating the workflow](#running--simulating-the-workflow)
  - [A. Simulate a verification locally with Docker](#a-simulate-a-verification-locally-with-docker)
  - [B. Run the metrics aggregation + dashboard locally](#b-run-the-metrics-aggregation--dashboard-locally)
  - [C. Trigger the live GitHub workflows](#c-trigger-the-live-github-workflows)
- [Configuration](#configuration)
- [The metrics marker](#the-metrics-marker)
- [One-time repository setup](#one-time-repository-setup)
- [Cost](#cost)
- [Limitations](#limitations)

---

## Why

Issue triage stalls not at intake (a bot auto-labels within minutes) but at the
**`validation:required` → human reproduction** step, which has no owner and no
SLA. Reproduction requires the same scarce committers who also do code review,
so reports sit for a long time before anyone confirms them.

This solution shifts the "build an environment + reproduce" work to Devin and
leaves humans with a **read-and-approve** task, freeing committer time and
shortening verification lead time from weeks/months to hours.

---

## Architecture

```
   New bug report                       ┌─────────────────────────────────────┐
        │                               │        GitHub repository            │
        ▼                               │                                     │
 dosubot auto-labels                    │  Issues = single source of truth    │
   validation:required                  │  (verification reports live in      │
        │                               │   issue comments, with a hidden     │
        ▼                               │   machine-readable metrics marker)  │
 ┌───────────────────────────┐         └─────────────────────────────────────┘
 │ devin-issue-verification   │                    ▲              │
 │ .yml  (on: issues.labeled) │  posts report      │              │ reads
 │  • adds devin:verifying    │  comment ──────────┘              │ markers
 │  • POST api.devin.ai       │                                   ▼
 │    /v1/sessions            │         ┌─────────────────────────────────────┐
 └───────────────────────────┘         │ devin-metrics-dashboard.yml          │
        │ starts                        │ (on: issues / issue_comment /        │
        ▼                               │  nightly cron / manual)              │
 ┌───────────────────────────┐         │  aggregate.py  → verifications.json  │
 │ Devin session              │         │  dashboard.html → index.html         │
 │ follows the playbook:      │         │  deploy → GitHub Pages               │
 │  Docker Superset + DB      │         └─────────────────────────────────────┘
 │  reproduce → evidence      │                          │
 │  → verification report     │                          ▼
 └───────────────────────────┘             https://<owner>.github.io/<repo>/
```

The key design choice: **the issues themselves are the source of truth.** Each
report comment embeds a hidden HTML-comment marker; the dashboard workflow
re-derives all metrics from those markers on every run. There is no database,
no append-only file, and therefore no write races or extra infrastructure.

---

## Components

| Path | Role |
| --- | --- |
| `.devin/playbooks/issue-verification.md` | The procedure Devin follows: extract facts → sufficiency gate → start Docker env → reproduce → analyze → post one report with a metrics marker. Encodes the hard rules (no code changes, no closing issues, 30-min budget). |
| `.github/workflows/devin-issue-verification.yml` | Trigger. On `issues.labeled` with `validation:required` (and open, not already verifying), adds `devin:verifying` and starts a Devin session via the Devin API. |
| `.github/workflows/devin-metrics-dashboard.yml` | Reporting. On issue/comment events, a nightly cron, or manual dispatch: runs the aggregator and deploys the dashboard to GitHub Pages. |
| `scripts/devin_verification_metrics/aggregate.py` | Pure-stdlib script. Reads all issues carrying the verification labels, extracts the metrics markers from their comments, and writes `verifications.json` (verdict distribution, reproduce rate, approval rate, runtime p50/p90, ACU total/avg, estimated cost). |
| `scripts/devin_verification_metrics/dashboard.html` | Static dashboard (Chart.js from CDN). Fetches `./verifications.json` and renders KPIs, charts, and a verification log. Deployed as `index.html`. |

---

## End-to-end flow

1. A user files a bug report; `dosubot` auto-labels it `validation:required`.
2. `devin-issue-verification.yml` fires, adds `devin:verifying`, and starts a
   Devin session pointed at the playbook and the issue URL.
3. Devin runs the playbook:
   - **Sufficiency gate** — if version / repro steps / expected-vs-actual are
     missing, it posts an `insufficient info` report with a copy-pasteable
     question for the reporter and proposes `requires:more-info`. It does **not**
     start an environment. (~2 min, ~0.4 ACU.)
   - Otherwise it starts **Superset in Docker** (plus Postgres/MySQL if needed),
     waits for `/health`, and reproduces the bug through the same surface as the
     reporter (UI or API), capturing screenshots, responses, logs, and stack
     traces.
   - It locates the likely faulty module and lists duplicate-issue candidates.
4. Devin posts **one** verification report comment ending in a hidden metrics
   marker. It never edits labels beyond the proposal, never closes the issue,
   and never opens a PR.
5. A human reads the report and **approves or rejects** it — recorded by editing
   the marker to add `"human_decision": "approved"` (or `"rejected"`).
6. `devin-metrics-dashboard.yml` re-aggregates and redeploys the dashboard.

---

## Labels

| Label | Meaning | Who sets it |
| --- | --- | --- |
| `validation:required` | Needs reproduction/verification. **Triggers** the solution. | `dosubot` at intake |
| `devin:verifying` | A Devin session is in progress (prevents re-triggering). | trigger workflow |
| `devin:reproduced` | Devin reproduced the bug. | human, after approval |
| `devin:not-reproduced` | Devin could not reproduce it. | human, after approval |
| `devin:needs-human` | External dependency / ambiguity requires a human. | human |
| `requires:more-info` | Report lacks the info needed to verify. | human, on Devin's proposal |

Notes: `validation:required` is applied automatically at intake and does **not**
guarantee the report is complete — the playbook's sufficiency gate handles that.
Closed issues can retain the label, so the trigger requires the issue to be
`open`.

---

## Running / simulating the workflow

There are three ways to exercise this solution. **A** and **B** run entirely on
your machine with no Devin API key and no GitHub write access.

### A. Simulate a verification locally with Docker

This mirrors what a Devin session does in step 3 — bring up Superset in Docker
and reproduce a bug by hand, following the playbook.

```bash
# From the repo root. Non-dev stack = prebuilt assets, publishes port 8088.
docker compose -f docker-compose-non-dev.yml up -d

# Wait for the backend to become healthy:
curl -f http://localhost:8088/health         # -> "OK"
```

Then open http://localhost:8088 (login `admin` / `admin`), follow the
reproduction steps from a `validation:required` issue, and collect evidence:

```bash
docker compose -f docker-compose-non-dev.yml logs --no-color superset | tail -n 200
```

The playbook (`.devin/playbooks/issue-verification.md`) is the exact,
step-by-step script a human or Devin can follow to produce a report. Tear down
with:

```bash
docker compose -f docker-compose-non-dev.yml down -v
```

> Alternatives: `docker-compose.yml` is the full dev stack (also on port 8088,
> with a hot-reloading webpack server on 9000); `docker-compose-light.yml` boots
> fastest but serves through the webpack proxy on **9001** (backend 8088 is
> container-internal). For a specific released version, use the published image:
> `docker run -p 8088:8088 apache/superset:<version>` and initialize it per the
> Superset docs.

### B. Run the metrics aggregation + dashboard locally

This mirrors the `devin-metrics-dashboard.yml` workflow. It only needs a GitHub
token with **read** access to the repo's issues.

```bash
# 1. Aggregate the metrics markers from the live issues into a JSON file.
GITHUB_TOKEN="$(gh auth token)" \
GITHUB_REPOSITORY="<owner>/<repo>" \
python3 scripts/devin_verification_metrics/aggregate.py /tmp/verifications.json

# 2. Serve the dashboard against that JSON.
mkdir -p /tmp/dash
cp scripts/devin_verification_metrics/dashboard.html /tmp/dash/index.html
cp /tmp/verifications.json /tmp/dash/verifications.json
python3 -m http.server 8000 --directory /tmp/dash
# open http://localhost:8000
```

`aggregate.py` uses only the Python standard library (no `pip install`). You can
override the ACU price with `DEVIN_ACU_USD` (default `2.25`).

### C. Trigger the live GitHub workflows

Once the workflows are on the repository's **default branch** (see
[one-time setup](#one-time-repository-setup)):

- **Verification** — add the `validation:required` label to an open issue. The
  trigger workflow starts a Devin session (needs `DEVIN_API_KEY`).
- **Dashboard** — runs automatically on issue/comment events and nightly. To
  force a refresh, use *Actions → Devin Verification Metrics → Run workflow*
  (`workflow_dispatch`), or any labeling/comment event on a tracked issue.

---

## Configuration

| Secret / variable | Where | Purpose |
| --- | --- | --- |
| `DEVIN_API_KEY` | repo Actions secret | Lets `devin-issue-verification.yml` start Devin sessions. |
| `GITHUB_TOKEN` | provided by Actions | Read issues (aggregator), write the `devin:verifying` label, deploy Pages. |
| `DEVIN_ACU_USD` | env var (optional) | ACU→USD rate for the cost estimate. Default `2.25`. |

---

## The metrics marker

Every verification report comment ends with a single hidden HTML comment. It is
invisible in the rendered issue but is what the dashboard reads:

```html
<!-- devin-metrics: {"issue": 4, "verdict": "reproduced", "runtime_min": 25, "acus": 6, "env": "Superset master (fae84ba) + postgres", "session": "https://app.devin.ai/sessions/..."} -->
```

Rules:

- Exactly one marker per report comment, valid single-line JSON.
- `verdict` ∈ `reproduced` | `not reproduced` | `insufficient info` |
  `needs human` | `unverified`.
- `runtime_min` = wall-clock verification minutes; `acus` = session ACU usage.
- `human_decision` is **not** set by Devin. A human adds
  `"human_decision": "approved"` or `"rejected"` when approving/rejecting; the
  dashboard uses it to compute the approval rate.

---

## One-time repository setup

The event-driven and scheduled workflows only run from the **default branch**,
so the solution must be merged to `master` (or `main`) to go live. In addition:

1. **Settings → Pages → Source = "GitHub Actions"** — enables dashboard
   deployment. The site publishes at `https://<owner>.github.io/<repo>/`.
2. **Settings → Secrets and variables → Actions → `DEVIN_API_KEY`** — required
   for the verification trigger.
3. *(optional)* **Settings → Security → enable Dependency graph** — only needed
   to satisfy the repository's `dependency-review` CI check; unrelated to this
   solution's behavior.

---

## Cost

Observed on the three-issue demo: **~10.4 ACU total** (~$23 at $2.25/ACU),
averaging **~3.5 ACU per verification**. The insufficient-info case exits at the
sufficiency gate in ~2 min / ~0.4 ACU. Live figures are always visible on the
dashboard.

---

## Limitations

- Bugs that depend on systems the environment cannot provision (Oracle, Druid,
  ClickHouse, Databricks, SSH tunnels, ...) are reported as `needs human`
  rather than reproduced.
- ACU values in markers may be estimates until the exact session figure is
  known.
- The human approve/reject signal currently relies on editing the marker; a
  future iteration could derive it from a dedicated label or comment command.
