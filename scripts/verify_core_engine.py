"""
scripts/verify_core_engine.py
------------------------------
End-to-end integration test and ASCII summary for TriageLens Phase 2 & 3.

Steps:
  1. Index demo_target_repo into ChromaDB (ast_indexer).
  2. Load + normalise all 8 tickets from data/sample_tickets.json.
  3. Cluster tickets (ticket_cluster).
  4. For each cluster: search top-3 suspect functions + resolve git blame owner.
  5. Print an ASCII summary table.
  6. Assert correctness with assert statements (exit 0 on pass, 1 on fail).

Usage:
    python scripts/verify_core_engine.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so `src.*` imports work regardless of
# how the script is invoked.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.ast_indexer import index_repository, search_suspect_functions
from src.ticket_cluster import normalize_ticket, cluster_tickets
from src.git_blame import suggest_owner_from_blame

# ---------------------------------------------------------------------------
# Expected ground-truth (for assertions)
# ---------------------------------------------------------------------------
EXPECTED_CLUSTER_COUNT = 4

# Maps cluster "representative subject keyword" -> expected top-1 function
# We assert on function_name only (not file) to be resilient to path changes.
EXPECTED_FUNCTION_BY_CLUSTER_SIZE = {
    3: "apply_promo",                 # 3 VIP25 tickets
    2: None,                          # 2-ticket clusters (checked by subject)
    1: "prorate_upgrade_charge",      # 1 subscription ticket
}

# Full ordered expectations: (expected_function_name, expected_github_handle, expected_commit_hash)
# Keyed by a keyword unique to each cluster's representative subject.
CLUSTER_EXPECTATIONS = [
    # keyword in rep subject -> (function_name, github_handle, commit_hash)
    ("vip",          "apply_promo",              "@aarav-payments", "e2b8ec1"),
    ("promo",        "apply_promo",              "@aarav-payments", "e2b8ec1"),
    ("shipping",     "resolve_free_shipping",    "@priya-checkout", "83227cc"),
    ("delivery",     "resolve_free_shipping",    "@priya-checkout", "83227cc"),
    ("link",         "check_magic_link_expiry",  "@rohan-auth",     "60acbe5"),
    ("magic",        "check_magic_link_expiry",  "@rohan-auth",     "60acbe5"),
    ("expired",      "check_magic_link_expiry",  "@rohan-auth",     "60acbe5"),
    ("billing",      "prorate_upgrade_charge",   "@sneha-billing",  "e4e9064"),
    ("prorate",      "prorate_upgrade_charge",   "@sneha-billing",  "e4e9064"),
    ("upgrad",       "prorate_upgrade_charge",   "@sneha-billing",  "e4e9064"),
    ("subscription", "prorate_upgrade_charge",   "@sneha-billing",  "e4e9064"),
    ("plan",         "prorate_upgrade_charge",   "@sneha-billing",  "e4e9064"),
]


_FUNC_TO_MODULE = {
    "apply_promo": "pricing.py",
    "resolve_free_shipping": "checkout.py",
    "check_magic_link_expiry": "auth_session.py",
    "prorate_upgrade_charge": "subscriptions.py",
}


def _get_active_commit_hash(func_name: str) -> str:
    """Read the latest 7-char commit hash for a function's module from git log."""
    module_file = _FUNC_TO_MODULE.get(func_name, "")
    if not module_file:
        return ""
    try:
        res = subprocess.run(
            ["git", "log", "-n", "1", "--format=%h", "--", f"demo_target_repo/shopflow/{module_file}"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        h = res.stdout.strip()
        if h:
            return h[:7]
    except Exception:
        pass
    return ""


def _find_expectation(rep_subject: str) -> tuple[str, str, str] | None:
    """Return (expected_func, expected_handle, expected_commit) matching the representative subject."""
    subj_lower = rep_subject.lower()
    for keyword, func, handle, commit in CLUSTER_EXPECTATIONS:
        if keyword in subj_lower:
            return func, handle, commit
    return None


# ---------------------------------------------------------------------------
# Pretty-print helpers (ASCII only -- Windows cp1252 safe)
# ---------------------------------------------------------------------------
COL_WIDTHS = {
    "cluster":    8,
    "dupes":      6,
    "priority":   10,
    "function":   28,
    "score":      7,
    "owner":      18,
    "commit":     8,
    "status":     8,
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
    return _row("Cluster", "Dupes", "Priority", "Top-1 Function",
                "Score", "Owner", "Commit", "Pass?")


# ---------------------------------------------------------------------------
# Main verification routine
# ---------------------------------------------------------------------------

def main() -> None:
    print("\n" + "=" * 70)
    print("  TriageLens -- Core Engine Verification")
    print("=" * 70)

    # ------------------------------------------------------------------
    # STEP 1: Index repository
    # ------------------------------------------------------------------
    print("\n[STEP 1] Indexing demo_target_repo into ChromaDB ...")
    repo_path = str(PROJECT_ROOT / "demo_target_repo")
    persist_dir = str(PROJECT_ROOT / "chroma_db")

    indexed = index_repository(repo_path=repo_path, persist_dir=persist_dir)
    print(f"[OK]   {len(indexed)} function(s) indexed.")

    assert len(indexed) > 0, "No functions indexed -- check demo_target_repo setup."

    # ------------------------------------------------------------------
    # STEP 2: Load + normalise tickets
    # ------------------------------------------------------------------
    print("\n[STEP 2] Loading tickets from data/sample_tickets.json ...")
    tickets_path = PROJECT_ROOT / "data" / "sample_tickets.json"

    with open(tickets_path, encoding="utf-8", errors="replace") as fh:
        raw_tickets = json.load(fh)

    normalised = [normalize_ticket(t) for t in raw_tickets]
    print(f"[OK]   {len(normalised)} tickets normalised.")

    sources = set(t["source"] for t in normalised)
    print(f"[INFO] Sources detected: {', '.join(sorted(sources))}")

    assert len(normalised) == 8, f"Expected 8 tickets, got {len(normalised)}"

    # ------------------------------------------------------------------
    # STEP 3: Cluster tickets
    # ------------------------------------------------------------------
    print("\n[STEP 3] Clustering tickets (threshold=0.58) ...")
    clusters = cluster_tickets(normalised, similarity_threshold=0.58)
    print(f"[OK]   {len(clusters)} cluster(s) formed.")

    # ------------------------------------------------------------------
    # STEP 4: Semantic search + git blame for each cluster
    # ------------------------------------------------------------------
    print("\n[STEP 4] Running semantic search + git blame per cluster ...")

    results_table: list[tuple] = []   # rows for summary table
    assertion_failures: list[str] = []

    for cluster in clusters:
        cid     = cluster["cluster_id"]
        dupes   = cluster["duplicate_count"]
        prio    = cluster["priority"]
        rep     = cluster["representative_ticket"]
        query   = cluster["combined_cluster_query"]

        # Semantic search
        matches = search_suspect_functions(query, top_k=3, persist_dir=persist_dir)

        if not matches:
            print(f"[WARN] {cid}: no matches returned for cluster query.")
            results_table.append((cid, dupes, prio, "N/A", 0.0, "N/A", "N/A", "FAIL"))
            assertion_failures.append(f"{cid}: search returned no results.")
            continue

        top_match = matches[0]
        func_name = top_match["function_name"]
        score     = top_match["similarity_score"]
        file_path = top_match["file_path"]
        start     = top_match["start_line"]
        end       = top_match["end_line"]

        # Git blame
        blame = suggest_owner_from_blame(
            file_path=file_path,
            start_line=start,
            end_line=end,
            repo_path=repo_path,
        )
        owner = blame["github_handle"]
        commit_hash = blame.get("commit_hash", "")

        # Validate against expectations
        rep_subject = rep["subject"]
        expectation = _find_expectation(rep_subject)
        passed = True

        if expectation is not None:
            exp_func, exp_owner, exp_commit = expectation
            if func_name != exp_func:
                msg = (
                    f"{cid}: expected top-1 function '{exp_func}', "
                    f"got '{func_name}' (score={score:.4f})"
                )
                assertion_failures.append(msg)
                passed = False
            if owner != exp_owner:
                msg = (
                    f"{cid}: expected owner '{exp_owner}', got '{owner}'"
                )
                assertion_failures.append(msg)
                passed = False
            active_commit = _get_active_commit_hash(func_name) or exp_commit
            if commit_hash != active_commit and commit_hash != exp_commit:
                msg = (
                    f"{cid}: expected commit hash '{active_commit}', got '{commit_hash}'"
                )
                assertion_failures.append(msg)
                passed = False

        row_status = "PASS" if passed else "FAIL"
        results_table.append((cid, dupes, prio, func_name, score, owner, commit_hash, row_status))

        print(
            f"  {cid}  dupes={dupes}  {prio}  "
            f"top1={func_name} ({score:.4f})  owner={owner}  commit={commit_hash}  [{row_status}]"
        )

    # ------------------------------------------------------------------
    # STEP 5: Print ASCII summary table
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  SUMMARY TABLE")
    print("=" * 70)
    print(_separator())
    print(_header())
    print(_separator())
    for row in results_table:
        cid, dupes, prio, func, score, owner, commit_h, status = row
        score_str = f"{score:.4f}" if isinstance(score, float) else str(score)
        print(_row(cid, str(dupes), prio, func, score_str, owner, commit_h, status))
    print(_separator())
    print()

    # ------------------------------------------------------------------
    # STEP 6: Assertions
    # ------------------------------------------------------------------
    print("[STEP 6] Running assertions ...")

    # 6a. Exactly 4 clusters
    assert len(clusters) == EXPECTED_CLUSTER_COUNT, (
        f"Expected {EXPECTED_CLUSTER_COUNT} clusters, got {len(clusters)}. "
        f"Cluster sizes: {[c['duplicate_count'] for c in clusters]}"
    )
    print(f"[OK]   Assertion 1/3: exactly {EXPECTED_CLUSTER_COUNT} clusters formed.")

    # 6b. Cluster sizes are {3, 2, 2, 1}
    actual_sizes = sorted([c["duplicate_count"] for c in clusters], reverse=True)
    expected_sizes = [3, 2, 2, 1]
    assert actual_sizes == expected_sizes, (
        f"Expected cluster sizes {expected_sizes}, got {actual_sizes}"
    )
    print(f"[OK]   Assertion 2/3: cluster sizes = {actual_sizes}.")

    # 6c. No top-1 function, owner, or commit mismatches
    if assertion_failures:
        print("\n[FAIL] The following assertions failed:")
        for msg in assertion_failures:
            print(f"       - {msg}")
        print()
        assert False, (
            f"{len(assertion_failures)} assertion failure(s). See above."
        )

    print("[OK]   Assertion 3/3: all top-1 functions, owners, and commit hashes match expected.")

    # ------------------------------------------------------------------
    # Done
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("  ALL ASSERTIONS PASSED -- TriageLens Core Engine: VERIFIED")
    print("=" * 70 + "\n")


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
