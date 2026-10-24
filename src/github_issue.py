"""
src/github_issue.py
-------------------
GitHub Issue formatter and publisher for TriageLens.

Transforms triaged cluster metadata, AST search rankings, git blame signals,
LLM reasoning, and Agent37 sandboxed reproduction artifacts into an
executive-grade GitHub Issue and publishes it via the GitHub REST API
(or persists a formatted Markdown artifact locally).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "adityakrmishra/triagelens")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")


def _is_placeholder(token: str) -> bool:
    """True if token is missing, empty, or a dummy placeholder."""
    if not token or not token.strip():
        return True
    lower = token.strip().lower()
    return lower.startswith("your_") or lower in ("none", "null", "placeholder", "test")


# ---------------------------------------------------------------------------
# Issue Payload Builder
# ---------------------------------------------------------------------------

def build_github_issue_payload(
    cluster: dict,
    suspect_functions: list[dict],
    owner_info: dict,
    llm_analysis: dict,
    repro_result: dict,
    override_owner: str | None = None,
) -> dict[str, Any]:
    """
    Format an executive-grade GitHub Issue payload from triaged components.

    Args:
        cluster:           Normalized ticket cluster dict.
        suspect_functions: List of top matched functions from ChromaDB.
        owner_info:        Git blame attribution dict from suggest_owner_from_blame.
        llm_analysis:      Dictionary with cluster_summary and match_reasons.
        repro_result:      Agent37 sandbox test result dict.
        override_owner:    Optional engineer override from UI.

    Returns:
        dict with `title`, `body` (Markdown), and `labels`.
    """
    priority = cluster.get("priority", "P1")
    dup_count = cluster.get("duplicate_count", len(cluster.get("tickets", [])))
    prio_reason = cluster.get("priority_reason", "")
    rep_ticket = cluster.get("representative_ticket", {})
    rep_subject = rep_ticket.get("subject", "Unspecified issue")

    top_fn = suspect_functions[0] if suspect_functions else {}
    top_fn_name = top_fn.get("function_name", "unknown_function")

    assigned_owner = override_owner or owner_info.get("github_handle", "@unassigned")
    author_name = owner_info.get("author_name", "Unknown")
    author_email = owner_info.get("author_email", "unknown@example.com")
    commit_hash = owner_info.get("commit_hash", "")
    commit_summary = owner_info.get("commit_summary", "")
    commit_date = owner_info.get("commit_date", "")
    ownership_pct = owner_info.get("ownership_pct", 1.0)

    summary_text = llm_analysis.get("cluster_summary", "")
    match_reasons = llm_analysis.get("match_reasons", {})

    agent37_status = repro_result.get("sandbox_status", "UNKNOWN")
    instance_id = repro_result.get("agent37_instance_id", "agent37-sandbox")
    failure_summary = repro_result.get("failure_summary", "Assertion error triggered in sandbox.")
    test_code = repro_result.get("test_code", "# No test code generated")
    pytest_stdout = repro_result.get("pytest_stdout", "")

    # Title
    title = f"[{priority}] Silent Bug in {top_fn_name}(): {rep_subject} ({dup_count} report(s))"

    # Labels
    labels = ["triagelens", priority.lower(), "silent-bug", "agent37-verified"]

    # Markdown Body Construction
    lines: list[str] = [
        "## 🔍 TriageLens Automated Triage Report",
        "",
        "| Attribute | Value |",
        "| :--- | :--- |",
        f"| **Priority Escalation** | **`{priority}`** ({prio_reason}) |",
        f"| **Customer Reports** | `{dup_count}` duplicate complaint(s) grouped |",
        f"| **Suggested Code Owner** | **{assigned_owner}** ({author_name} &lt;{author_email}&gt;) |",
        f"| **Agent37 Sandbox Status** | **`{agent37_status}`** (Instance `{instance_id}`) |",
        "",
        "---",
        "",
        "### 📋 Executive Summary",
        summary_text,
        "",
        "---",
        "",
        "### 🎯 Shortlisted Suspect Functions (Python AST + Semantic Indexing)",
        "",
    ]

    for rank, fn in enumerate(suspect_functions[:3], 1):
        f_name = fn.get("function_name", "")
        f_path = fn.get("file_path", "")
        s_line = fn.get("start_line", 1)
        e_line = fn.get("end_line", 1)
        sig = fn.get("signature", f"def {f_name}()")
        score = fn.get("similarity_score", 0.0)

        # Build GitHub permalink
        clean_fpath = f_path.replace("\\", "/").strip("/")
        if not clean_fpath.startswith("demo_target_repo/"):
            repo_rel_path = f"demo_target_repo/{clean_fpath}"
        else:
            repo_rel_path = clean_fpath

        repo = os.getenv("GITHUB_REPO", GITHUB_REPO).strip()
        branch = os.getenv("GITHUB_BRANCH", GITHUB_BRANCH).strip()
        permalink = (
            f"https://github.com/{repo}/blob/{branch}/"
            f"{repo_rel_path}#L{s_line}-L{e_line}"
        )

        reason = match_reasons.get(
            f_name,
            f"Semantic similarity {score:.2%} against cluster complaints."
        )

        lines.extend([
            f"#### Rank #{rank}: [`{f_name}`]({permalink}) ({score:.2%} match)",
            f"- **File**: [`{f_path}#L{s_line}-L{e_line}`]({permalink})",
            f"- **Signature**: `{sig}`",
            f"- **Match Rationale**: {reason}",
            "",
        ])

    lines.extend([
        "---",
        "",
        "### 👤 Suggested Code Owner (`git blame` Signal)",
        f"- **Primary Engineer**: **{assigned_owner}** ({author_name} &lt;{author_email}&gt;)",
        f"- **Line Ownership**: `{ownership_pct:.1%}` of function implementation",
        f"- **Planted / Latest Commit**: `{commit_hash}` — *\"{commit_summary}\"* (`{commit_date}`)",
        "",
        "---",
        "",
        f"### 🧪 Agent37 Isolated Sandbox Reproduction (`Instance: {instance_id}`)",
        "- **Sandbox Platform**: [Agent37 Cloud Sandbox](https://api.agent37.com) (`sandbox.agent37.app`)",
        "- **Verification Protocol**: Automated `pytest` execution verifying silent logic bug divergence (`AssertionError`)",
        "",
        f"> **Assertion Result**: {failure_summary}",
        "",
        "<details>",
        "<summary><b>View Generated pytest Reproduction Code</b></summary>",
        "",
        "```python",
        test_code.strip(),
        "```",
        "",
        "</details>",
        "",
        "<details open>",
        "<summary><b>Agent37 Pytest Execution Log</b></summary>",
        "",
        "```text",
        pytest_stdout.strip(),
        "```",
        "",
        "</details>",
        "",
        "---",
        "",
        f"### 📑 Grouped Customer Support Tickets ({dup_count})",
        "",
        "| Ticket ID | Source | Customer | Subject / Summary |",
        "| :--- | :--- | :--- | :--- |",
    ])

    for t in cluster.get("tickets", []):
        tid = t.get("ticket_id", "")
        src = t.get("source", "Ticket")
        cname = t.get("customer_name") or "Anonymous"
        cemail = t.get("customer_email") or ""
        cust_str = f"{cname} ({cemail})" if cemail else cname
        subj = t.get("subject", "").replace("|", "-")
        lines.append(f"| `{tid}` | {src} | {cust_str} | {subj} |")

    lines.extend([
        "",
        "---",
        "*Automated with [TriageLens](https://github.com/adityakrmishra/triagelens) — AST Semantic Indexing + Local Embeddings + Agent37 Sandboxed Bug Reproduction.*",
    ])

    body = "\n".join(lines)

    return {
        "title": title,
        "body": body,
        "labels": labels,
        "cluster_id": cluster.get("cluster_id", "CLU-001"),
        "suggested_owner": assigned_owner,
    }


# ---------------------------------------------------------------------------
# GitHub Issue Publisher
# ---------------------------------------------------------------------------

def publish_github_issue(
    issue_payload: dict,
    cluster_id: str,
    output_dir: str = "output_issues",
) -> dict[str, Any]:
    """
    Publish an issue to GitHub and always save a local Markdown artifact.

    Args:
        issue_payload: Dict containing `title`, `body`, and `labels`.
        cluster_id:    Unique cluster identifier (e.g. 'CLU-001').
        output_dir:    Local directory to persist Markdown artifacts.

    Returns:
        dict with `status` ('published' | 'saved_locally'), `local_path`,
        and optional `issue_url` and `issue_number`.
    """
    # 1. Always persist local Markdown copy
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    local_file = out_path / f"issue_{cluster_id}.md"

    md_content = (
        f"# {issue_payload.get('title', '')}\n\n"
        f"**Labels:** {', '.join(issue_payload.get('labels', []))}\n\n"
        f"{issue_payload.get('body', '')}\n"
    )
    local_file.write_text(md_content, encoding="utf-8", errors="replace")

    # 2. Attempt GitHub REST API publishing if token is present
    active_token = os.getenv("GITHUB_TOKEN", GITHUB_TOKEN).strip()
    active_repo = os.getenv("GITHUB_REPO", GITHUB_REPO).strip()
    if not _is_placeholder(active_token):
        try:
            import requests

            url = f"https://api.github.com/repos/{active_repo}/issues"
            headers = {
                "Authorization": f"Bearer {active_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            body = {
                "title": issue_payload.get("title", ""),
                "body": issue_payload.get("body", ""),
                "labels": issue_payload.get("labels", []),
            }

            resp = requests.post(url, headers=headers, json=body, timeout=12)

            if resp.status_code == 201:
                data = resp.json()
                issue_url = data.get("html_url", "")
                issue_number = data.get("number", 0)
                print(f"[OK]   GitHub issue published: {issue_url}")
                return {
                    "status": "published",
                    "issue_url": issue_url,
                    "issue_number": issue_number,
                    "local_path": local_file.as_posix(),
                }
            else:
                print(
                    f"[WARN] GitHub API returned status {resp.status_code}: "
                    f"{resp.text[:120]}. Falling back to local artifact."
                )

        except Exception as exc:
            print(f"[WARN] GitHub publishing failed: {exc}. Saved locally.")

    print(f"[OK]   Issue artifact saved locally at {local_file.as_posix()}")
    return {
        "status": "saved_locally",
        "local_path": local_file.as_posix(),
        "issue_url": None,
        "issue_number": None,
    }
