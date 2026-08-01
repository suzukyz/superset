#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Aggregate Devin issue-verification metrics from GitHub issue comments.

Source of truth: the issues themselves. Every automated verification report
posted by the solution embeds a hidden, machine-readable marker of the form:

    <!-- devin-metrics: {"issue": 4, "verdict": "reproduced", ...} -->

This script reads all issues carrying the verification labels, extracts those
markers from their comments, and writes a single ``verifications.json`` that
the GitHub Pages dashboard renders. Because it recomputes from the issues on
every run, there is no append-only file to keep in sync and no write races.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

API_ROOT = os.environ.get("GITHUB_API_URL", "https://api.github.com")
MARKER_RE = re.compile(r"<!--\s*devin-metrics:\s*(\{.*?\})\s*-->", re.DOTALL)

# Labels that mark an issue as being (or having been) in the verification flow.
TRACKED_LABELS = [
    "validation:required",
    "devin:verifying",
    "devin:reproduced",
    "devin:not-reproduced",
    "devin:needs-human",
    "requires:more-info",
]

VALID_VERDICTS = {
    "reproduced",
    "not reproduced",
    "insufficient info",
    "needs human",
    "unverified",
}


def _request(url: str, token: str) -> tuple[Any, dict[str, str]]:
    req = urllib.request.Request(url)  # noqa: S310
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
        headers = {k.lower(): v for k, v in resp.headers.items()}
        return payload, headers


def _paginate(url: str, token: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    next_url: str | None = url
    while next_url:
        payload, headers = _request(next_url, token)
        if isinstance(payload, list):
            results.extend(payload)
        link = headers.get("link", "")
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        next_url = match.group(1) if match else None
    return results


def collect_issue_numbers(repo: str, token: str) -> set[int]:
    numbers: set[int] = set()
    for label in TRACKED_LABELS:
        query = urllib.parse.urlencode(
            {"state": "all", "labels": label, "per_page": "100"}
        )
        url = f"{API_ROOT}/repos/{repo}/issues?{query}"
        for issue in _paginate(url, token):
            # The issues endpoint also returns PRs; skip them.
            if "pull_request" in issue:
                continue
            numbers.add(int(issue["number"]))
    return numbers


def parse_markers(body: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in MARKER_RE.findall(body or ""):
        try:
            records.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return records


def gather_records(repo: str, token: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number in sorted(collect_issue_numbers(repo, token)):
        comments_url = f"{API_ROOT}/repos/{repo}/issues/{number}/comments?per_page=100"
        for comment in _paginate(comments_url, token):
            for marker in parse_markers(comment.get("body", "")):
                marker.setdefault("issue", number)
                marker.setdefault("created_at", comment.get("created_at"))
                marker["comment_url"] = comment.get("html_url")
                records.append(marker)
    return records


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 2)


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    verdict_counts: dict[str, int] = {}
    runtimes: list[float] = []
    acus: list[float] = []
    approved = rejected = pending = 0

    for rec in records:
        verdict = str(rec.get("verdict", "unknown")).lower()
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        if isinstance(rec.get("runtime_min"), (int, float)):
            runtimes.append(float(rec["runtime_min"]))
        if isinstance(rec.get("acus"), (int, float)):
            acus.append(float(rec["acus"]))
        human = str(rec.get("human_decision", "pending")).lower()
        if human == "approved":
            approved += 1
        elif human == "rejected":
            rejected += 1
        else:
            pending += 1

    verifiable = [
        r
        for r in records
        if str(r.get("verdict", "")).lower() in {"reproduced", "not reproduced"}
    ]
    reproduced = verdict_counts.get("reproduced", 0)
    decided = approved + rejected
    total_acus = round(sum(acus), 2)
    acu_rate = float(os.environ.get("DEVIN_ACU_USD", "2.25"))

    return {
        "total_verifications": total,
        "verdict_counts": verdict_counts,
        "reproduce_rate": (
            round(reproduced / len(verifiable), 3) if verifiable else None
        ),
        "human_approval_rate": (round(approved / decided, 3) if decided else None),
        "human_decisions": {
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
        },
        "runtime_min": {
            "avg": round(sum(runtimes) / len(runtimes), 2) if runtimes else 0.0,
            "p50": _percentile(runtimes, 0.50),
            "p90": _percentile(runtimes, 0.90),
        },
        "acus": {
            "total": total_acus,
            "avg": round(sum(acus) / len(acus), 2) if acus else 0.0,
            "est_cost_usd": round(total_acus * acu_rate, 2),
            "usd_per_acu": acu_rate,
        },
    }


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    out_path = sys.argv[1] if len(sys.argv) > 1 else "verifications.json"
    if not token or not repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY are required", file=sys.stderr)
        return 1

    records = gather_records(repo, token)
    records.sort(key=lambda r: str(r.get("created_at") or ""))
    document = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "summary": summarize(records),
        "records": records,
    }
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
    print(f"Wrote {out_path}: {len(records)} verification record(s) from {repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
