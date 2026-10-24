"""
src/git_blame.py
----------------
Git blame runner and owner resolver for TriageLens.

Given a file path and a line range inside the demo target repository,
executes `git blame --line-porcelain` and parses the output to identify
the primary code owner, then maps the email to a GitHub handle.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Email -> GitHub handle mapping for the ShopFlow demo team
# ---------------------------------------------------------------------------
_EMAIL_TO_HANDLE: dict[str, str] = {
    "aarav@shopflow.io":  "@aarav-payments",
    "priya@shopflow.io":  "@priya-checkout",
    "rohan@shopflow.io":  "@rohan-auth",
    "sneha@shopflow.io":  "@sneha-billing",
}


def _email_to_slug(email: str) -> str:
    """
    Fallback: derive a GitHub-style handle from an email local-part.
    E.g. 'john.doe@example.com' -> '@john-doe'
    """
    local = email.split("@")[0]
    slug = re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-")
    return f"@{slug}"


def _resolve_handle(email: str) -> str:
    """Return the known GitHub handle or generate a slug fallback."""
    return _EMAIL_TO_HANDLE.get(email.lower(), _email_to_slug(email))


# ---------------------------------------------------------------------------
# porcelain parser
# ---------------------------------------------------------------------------

def _parse_blame_porcelain(output: str) -> list[dict]:
    """
    Parse the output of `git blame --line-porcelain` into a list of
    per-line attribution records.

    Each record contains:
        commit_hash, author, author_mail, author_time (int epoch),
        summary, line_number (1-indexed), line_content.
    """
    records: list[dict] = []
    current: dict = {}

    for raw_line in output.splitlines():
        # A 40-char hex hash followed by 3 integers starts a new record
        if re.match(r"^[0-9a-f]{40} ", raw_line):
            if current:
                records.append(current)
            parts = raw_line.split()
            current = {
                "commit_hash":  parts[0],
                "line_number":  int(parts[2]),
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
            current["line_content"] = raw_line[1:]   # strip leading tab

    if current:
        records.append(current)

    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_owner_from_blame(
    file_path: str,
    start_line: int,
    end_line: int,
    repo_path: str = "demo_target_repo",
) -> dict:
    """
    Run `git blame --line-porcelain -L start,end file_path` inside repo_path
    and determine the primary code owner for that line range.

    Args:
        file_path:   Relative path inside the repo (e.g. 'shopflow/pricing.py').
        start_line:  First line of the function (1-indexed).
        end_line:    Last line of the function (1-indexed, inclusive).
        repo_path:   Path to the git repository root directory.

    Returns:
        dict with keys:
          github_handle, author_name, author_email,
          commit_hash (7-char short), commit_summary, commit_date (ISO),
          ownership_pct (float 0-1), all_contributors (list[dict]).
    """
    repo_root = Path(repo_path).resolve() if repo_path else Path.cwd().resolve()

    # Locate actual file on disk across common relative/nested layouts
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

    # ----- Aggregate line counts per author email -----
    line_counts: Counter = Counter()
    commit_by_email: dict[str, dict] = {}

    for rec in records:
        email = rec.get("author_mail", "unknown@unknown")
        line_counts[email] += 1

        # Keep the most recent commit per email (highest author_time)
        existing_time = commit_by_email.get(email, {}).get("author_time", 0)
        if rec.get("author_time", 0) >= existing_time:
            commit_by_email[email] = rec

    total_lines = len(records)

    # Primary owner = email with most lines
    primary_email, primary_count = line_counts.most_common(1)[0]
    primary_rec = commit_by_email[primary_email]

    # Format commit date
    ts = primary_rec.get("author_time", 0)
    try:
        commit_date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (OSError, OverflowError):
        commit_date = "unknown"

    # Build all_contributors list (sorted by line count desc)
    all_contributors: list[dict] = []
    for email, count in line_counts.most_common():
        rec = commit_by_email[email]
        all_contributors.append({
            "github_handle": _resolve_handle(email),
            "author_name":   rec.get("author", "Unknown"),
            "author_email":  email,
            "line_count":    count,
            "ownership_pct": round(count / total_lines, 4),
        })

    return {
        "github_handle":  _resolve_handle(primary_email),
        "author_name":    primary_rec.get("author", "Unknown"),
        "author_email":   primary_email,
        "commit_hash":    primary_rec.get("commit_hash", "")[:7],
        "commit_summary": primary_rec.get("summary", ""),
        "commit_date":    commit_date,
        "ownership_pct":  round(primary_count / total_lines, 4),
        "all_contributors": all_contributors,
        "error": None,
    }


def _error_result(message: str) -> dict:
    """Return a standardised error dict when blame cannot be run."""
    print(f"[WARN] git_blame: {message}")
    return {
        "github_handle":    "@unknown",
        "author_name":      "Unknown",
        "author_email":     "unknown",
        "commit_hash":      "0000000",
        "commit_summary":   "",
        "commit_date":      "",
        "ownership_pct":    0.0,
        "all_contributors": [],
        "error":            message,
    }
