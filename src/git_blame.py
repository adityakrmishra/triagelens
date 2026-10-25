"""
Git blame parser and code ownership attribution.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_EMAIL_TO_HANDLE: dict[str, str] = {
    "aarav@shopflow.io": "@aarav-payments",
    "priya@shopflow.io": "@priya-checkout",
    "rohan@shopflow.io": "@rohan-auth",
    "sneha@shopflow.io": "@sneha-billing",
}


def _email_to_slug(email: str) -> str:
    local = email.split("@")[0]
    slug = re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-")
    return f"@{slug}"


def _resolve_handle(email: str) -> str:
    return _EMAIL_TO_HANDLE.get(email.lower(), _email_to_slug(email))


def _parse_blame_porcelain(output: str) -> list[dict]:
    records: list[dict] = []
    current: dict = {}

    for raw_line in output.splitlines():
        if re.match(r"^[0-9a-f]{40} ", raw_line):
            if current:
                records.append(current)
            parts = raw_line.split()
            current = {
                "commit_hash": parts[0],
                "line_number": int(parts[2]),
            }
        elif raw_line.startswith("author "):
            current["author"] = raw_line[len("author "):].strip()
        elif raw_line.startswith("author-mail "):
            mail = raw_line[len("author-mail "):].strip().strip("<>")
            current["author_mail"] = mail
        elif raw_line.startswith("author-time "):
            current["author_time"] = int(raw_line[len("author-time "):].strip())
        elif raw_line.startswith("summary "):
            current["summary"] = raw_line[len("summary "):].strip()
        elif raw_line.startswith("\t"):
            current["line_content"] = raw_line[1:]

    if current:
        records.append(current)

    return records


def suggest_owner_from_blame(
    file_path: str,
    start_line: int,
    end_line: int,
    repo_path: str = "demo_target_repo",
) -> dict:
    repo_root = Path(repo_path).resolve() if repo_path else Path.cwd().resolve()

    norm_fp = file_path.replace("\\", "/").strip("/")
    candidate_paths = [
        repo_root / norm_fp,
        Path.cwd() / norm_fp,
        repo_root / "demo_target_repo" / norm_fp,
        Path.cwd() / "demo_target_repo" / norm_fp,
    ]
    if norm_fp.startswith("demo_target_repo/"):
        stripped = norm_fp[len("demo_target_repo/"):]
        candidate_paths.extend([
            repo_root / stripped,
            Path.cwd() / stripped,
        ])

    actual_file: Path | None = None
    for cand in candidate_paths:
        if cand.is_file():
            actual_file = cand.resolve()
            break

    if actual_file is not None:
        cmd = [
            "git", "-C", str(actual_file.parent),
            "blame", "--line-porcelain",
            f"-L{start_line},{end_line}",
            actual_file.name,
        ]
    else:
        cmd = [
            "git", "-C", str(repo_root),
            "blame", "--line-porcelain",
            f"-L{start_line},{end_line}",
            file_path,
        ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except FileNotFoundError:
        return _error_result("git executable not found on PATH.")
    except subprocess.TimeoutExpired:
        return _error_result("git blame timed out.")

    if result.returncode != 0:
        return _error_result(
            f"git blame failed (exit {result.returncode}): {result.stderr[:200]}"
        )

    records = _parse_blame_porcelain(result.stdout)
    if not records:
        return _error_result("No blame records parsed (empty output).")

    line_counts: Counter = Counter()
    commit_by_email: dict[str, dict] = {}

    for rec in records:
        email = rec.get("author_mail", "unknown@unknown")
        line_counts[email] += 1
        existing_time = commit_by_email.get(email, {}).get("author_time", 0)
        if rec.get("author_time", 0) >= existing_time:
            commit_by_email[email] = rec

    total_lines = len(records)
    primary_email, primary_count = line_counts.most_common(1)[0]
    primary_rec = commit_by_email[primary_email]

    ts = primary_rec.get("author_time", 0)
    try:
        commit_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OSError, OverflowError):
        commit_date = "unknown"

    all_contributors: list[dict] = []
    for email, count in line_counts.most_common():
        rec = commit_by_email[email]
        all_contributors.append({
            "github_handle": _resolve_handle(email),
            "author_name": rec.get("author", "Unknown"),
            "author_email": email,
            "line_count": count,
            "ownership_pct": round(count / total_lines, 4),
        })

    return {
        "github_handle": _resolve_handle(primary_email),
        "author_name": primary_rec.get("author", "Unknown"),
        "author_email": primary_email,
        "commit_hash": primary_rec.get("commit_hash", "")[:7],
        "commit_summary": primary_rec.get("summary", ""),
        "commit_date": commit_date,
        "ownership_pct": round(primary_count / total_lines, 4),
        "all_contributors": all_contributors,
        "error": None,
    }


def _error_result(message: str) -> dict:
    return {
        "github_handle": "@unknown",
        "author_name": "Unknown",
        "author_email": "unknown",
        "commit_hash": "0000000",
        "commit_summary": "",
        "commit_date": "",
        "ownership_pct": 0.0,
        "all_contributors": [],
        "error": message,
    }
