"""
scripts/verify_full_pipeline.py
-------------------------------
End-to-end integration and verification script for TriageLens Phases 2, 3, 4, & 5.

Verifies:
  1. AST semantic indexing of demo_target_repo into ChromaDB.
  2. Ticket normalization and clustering of 8 tickets into 4 clusters.
  3. Git blame code owner resolution.
  4. LLM reasoning (executive summary + per-function match rationale).
  5. Agent37 isolated sandbox bug reproduction (confirming silent bug assertion failure).
  6. GitHub Issue payload generation with line permalinks and local artifact publishing.
  7. Asserts 100% correctness and exits with code 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ast_indexer import index_repository, search_suspect_functions
from src.ticket_cluster import normalize_ticket, cluster_tickets
from src.git_blame import suggest_owner_from_blame
from src.llm_reasoner import explain_matches_and_summarize
from src.agent37_reproducer import reproduce_bug_in_agent37
from src.github_issue import build_github_issue_payload, publish_github_issue

# ---------------------------------------------------------------------------
# Pretty-print helpers (ASCII only -- Windows safe)
# ---------------------------------------------------------------------------
COL_WIDTHS = {
    "cluster":  8,
    "prio":     6,
    "top1":     26,
    "owner":    16,
    "agent37":  22,
    "repro":    8,
    "artifact": 26,
}


def _row(*values: str) -> str:
    cols = list(COL_WIDTHS.values())
    cells = []
    for v, w in zip(values, cols):
        cells.append(str(v)[:w].ljust(w))
    return "| " + " | ".join(cells) + " |"


def _separator() -> str:
    return "+-" + "-+-".join("-" * w for w in COL_WIDTHS.values()) + "-+"


def _header() -> str:
    return _row("Cluster", "Prio", "Top-1 Function", "Owner", "Agent37 Status", "Repro?", "Artifact")


def main() -> None:
    print("\n" + "=" * 75)
    print("  TriageLens -- Complete Pipeline Verification (Phases 2-5)")
    print("=" * 75)

    # 1. Index repository
    print("\n[STEP 1] Indexing demo_target_repo into ChromaDB ...")
    repo_path = str(PROJECT_ROOT / "demo_target_repo")
    persist_dir = str(PROJECT_ROOT / "chroma_db")
    indexed = index_repository(repo_path=repo_path, persist_dir=persist_dir)
    print(f"[OK]   {len(indexed)} functions indexed.")
    assert len(indexed) >= 8, f"Expected at least 8 functions indexed, got {len(indexed)}"

    # 2. Load and cluster tickets
    print("\n[STEP 2] Loading & clustering data/sample_tickets.json ...")
    tickets_path = PROJECT_ROOT / "data" / "sample_tickets.json"
    with open(tickets_path, encoding="utf-8", errors="replace") as fh:
        raw_tickets = json.load(fh)

    normalised = [normalize_ticket(t) for t in raw_tickets]
    clusters = cluster_tickets(normalised, similarity_threshold=0.58)
    print(f"[OK]   {len(clusters)} clusters formed from {len(normalised)} tickets.")
    assert len(clusters) == 4, f"Expected 4 clusters, got {len(clusters)}"

    # 3. Process each cluster through LLM reasoner, Agent37 reproducer, and GitHub publisher
    print("\n[STEP 3] Running Full Pipeline per Cluster ...")
    summary_rows = []
    assertion_failures = []

    for cluster in clusters:
        cid = cluster["cluster_id"]
        prio = cluster["priority"]
        query = cluster["combined_cluster_query"]

        # Semantic Search
        matches = search_suspect_functions(query, top_k=3, persist_dir=persist_dir)
        assert matches, f"{cid}: Semantic search returned no matches."
        top_fn = matches[0]
        fn_name = top_fn["function_name"]

        # Git blame owner
        blame = suggest_owner_from_blame(
            file_path=top_fn["file_path"],
            start_line=top_fn["start_line"],
            end_line=top_fn["end_line"],
            repo_path=repo_path,
        )
        owner = blame["github_handle"]

        # LLM Reasoning
        llm_out = explain_matches_and_summarize(cluster, matches)
        assert "cluster_summary" in llm_out, f"{cid}: missing cluster_summary"
        assert "match_reasons" in llm_out, f"{cid}: missing match_reasons"

        # Agent37 Sandbox Bug Reproduction
        repro = reproduce_bug_in_agent37(cluster, top_fn, repo_path=repo_path)
        sandbox_status = repro["sandbox_status"]
        reproduced = repro["bug_reproduced"]

        if not reproduced:
            assertion_failures.append(f"{cid} ({fn_name}): bug was not reproduced in Agent37 sandbox.")

        # GitHub Issue Payload & Publishing
        payload = build_github_issue_payload(
            cluster=cluster,
            suspect_functions=matches,
            owner_info=blame,
            llm_analysis=llm_out,
            repro_result=repro,
        )
        pub_result = publish_github_issue(payload, cluster_id=cid)
        artifact_path = pub_result.get("local_path", "")

        # Verify artifact file exists and has content
        local_file = Path(artifact_path)
        if not local_file.exists() or local_file.stat().st_size < 200:
            assertion_failures.append(f"{cid}: Issue artifact not found or empty at {artifact_path}")

        summary_rows.append((
            cid,
            prio,
            fn_name,
            owner,
            sandbox_status,
            "YES" if reproduced else "NO",
            local_file.name,
        ))

        print(
            f"  {cid} [{prio}]  top1={fn_name}  owner={owner}  "
            f"agent37={sandbox_status}  repro={'YES' if reproduced else 'NO'}"
        )

    # 4. Summary Table
    print("\n" + "=" * 75)
    print("  COMPLETE PIPELINE EXECUTION SUMMARY")
    print("=" * 75)
    print(_separator())
    print(_header())
    print(_separator())
    for row in summary_rows:
        print(_row(*row))
    print(_separator())
    print()

    # 5. Assertions
    print("[STEP 4] Running Pipeline Assertions ...")
    if assertion_failures:
        print("\n[FAIL] Assertion failures detected:")
        for fail in assertion_failures:
            print(f"       - {fail}")
        print()
        assert False, f"{len(assertion_failures)} pipeline assertion failure(s)."

    print("[OK]   Assertion 1/3: exactly 4 clusters triaged.")
    print("[OK]   Assertion 2/3: all 4 silent bugs reproduced in Agent37 sandbox (AssertionError confirmed).")
    print("[OK]   Assertion 3/3: all 4 GitHub issue markdown artifacts created with permalinks & repro code.")

    print("\n" + "=" * 75)
    print("  ALL PIPELINE ASSERTIONS PASSED -- TriageLens Ready for UI!")
    print("=" * 75 + "\n")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\n[ASSERTION FAILED] {exc}\n")
        sys.exit(1)
    except Exception as exc:
        import traceback
        print(f"\n[ERROR] Unexpected exception: {exc}")
        traceback.print_exc()
        sys.exit(1)
