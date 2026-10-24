"""
src/ticket_cluster.py
---------------------
Ticket normalisation, embedding, and cosine-similarity clustering.

Supports both Zendesk v2 and Jira v3 webhook JSON schemas.

Public API:
  normalize_ticket(raw_ticket: dict) -> dict
  cluster_tickets(tickets: list[dict], similarity_threshold: float) -> list[dict]
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from src.embedder import embed_texts

# ---------------------------------------------------------------------------
# Business-impact keyword weights for severity scoring
# ---------------------------------------------------------------------------
_SEVERITY_KEYWORDS: dict[str, int] = {
    # Payment / billing -- highest impact
    "payment":    4,
    "charged":    4,
    "billing":    4,
    "invoice":    4,
    "refund":     4,
    "overcharge": 4,
    "prorated":   3,
    "prorate":    3,
    "upgrade":    2,
    # Checkout / cart
    "checkout":   3,
    "cart":       2,
    "shipping":   3,
    "delivery":   2,
    "free shipping": 3,
    # Discount / promo
    "discount":   3,
    "promo":      3,
    "coupon":     3,
    "vip":        3,
    "vip25":      3,
    "clearance":  2,
    "sale":       2,
    # Auth / login
    "login":      3,
    "magic link": 4,
    "magic-link": 4,
    "expired":    3,
    "expiry":     3,
    "locked out": 4,
    "lockout":    4,
    "password":   2,
    # Generic urgency
    "urgent":     3,
    "critical":   3,
    "broken":     2,
    "not working": 2,
    "bug":        2,
}

_BILLING_CRITICAL_KEYWORDS = {
    "payment", "charged", "billing", "invoice", "refund",
    "overcharge", "prorated", "prorate", "magic link", "magic-link",
    "locked out", "lockout",
}


# ---------------------------------------------------------------------------
# Schema detection helpers
# ---------------------------------------------------------------------------

def _is_zendesk(raw: dict) -> bool:
    """True if the JSON looks like a Zendesk v2 ticket."""
    if raw.get("_schema") == "zendesk_v2":
        return True
    if "ticket" in raw and isinstance(raw["ticket"], dict):
        return True
    return (
        "subject" in raw
        and ("description" in raw or "body" in raw)
        and ("requester" in raw or "created_at" in raw or "id" in raw)
    )


def _is_jira(raw: dict) -> bool:
    """True if the JSON looks like a Jira v3 issue."""
    if raw.get("_schema") == "jira_v3":
        return True
    if "issue" in raw and isinstance(raw["issue"], dict):
        return True
    return (
        "summary" in raw
        or "issueType" in raw
        or "key" in raw
        or (
            "fields" in raw
            and "summary" in raw.get("fields", {})
        )
        or (isinstance(raw.get("id", ""), str) and "-" in str(raw.get("id", "")))
    )


# ---------------------------------------------------------------------------
# Payload Parsing
# ---------------------------------------------------------------------------

def parse_tickets_payload(payload: dict | list) -> list[dict]:
    """
    Parse an incoming ticket JSON payload into a list of ticket dicts.

    Handles:
      - list of ticket dicts: `[ {...}, {...} ]` -> returns list
      - dict with 'tickets' key: `{"tickets": [ {...}, ... ]}` -> returns list
      - dict with 'issues' key: `{"issues": [ {...}, ... ]}` -> returns list
      - single raw ticket dict: `{"id": 123, ...}` or `{"ticket": ...}` -> returns `[ payload ]`
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        if "tickets" in payload and isinstance(payload["tickets"], list):
            return [t for t in payload["tickets"] if isinstance(t, dict)]
        if "issues" in payload and isinstance(payload["issues"], list):
            return [t for t in payload["issues"] if isinstance(t, dict)]
        return [payload]

    return []


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize_ticket(raw_ticket: dict) -> dict:
    """
    Normalise a raw Zendesk or Jira webhook payload into a unified dict.

    Handles both:
      - Zendesk v2: keys `id`, `subject`, `description`, `requester`,
                    `tags`, `created_at` (or nested under `ticket`)
      - Jira v3:    keys `key`/`id`, `summary`, `description`, `reporter`,
                    `labels`, `created` (or nested under `issue` / `fields`)

    Returns a dict with:
      ticket_id, source, subject, body, customer_name, customer_email,
      created_at, tags (list[str]), combined_text.
    """
    # --- Detect schema ---
    if _is_zendesk(raw_ticket):
        t = raw_ticket.get("ticket", raw_ticket)
        source = "Zendesk"
        ticket_id = str(t.get("id", raw_ticket.get("id", "")))
        subject   = t.get("subject", "") or ""
        body      = t.get("description", t.get("body", "")) or ""
        requester = t.get("requester", {}) or {}
        customer_name  = requester.get("name", "") or ""
        customer_email = requester.get("email", "") or ""
        created_at = t.get("created_at", "") or ""
        tags = t.get("tags", []) or []

    elif _is_jira(raw_ticket):
        issue = raw_ticket.get("issue", raw_ticket)
        fields = issue.get("fields", issue)
        source = "Jira"
        ticket_id = str(
            issue.get("key")
            or raw_ticket.get("key")
            or issue.get("id")
            or raw_ticket.get("id", "")
        )
        subject    = fields.get("summary", issue.get("summary", "")) or ""
        body       = fields.get("description", issue.get("description", "")) or ""
        reporter   = fields.get("reporter", issue.get("reporter", {})) or {}
        customer_name  = (
            reporter.get("displayName", "")
            or reporter.get("name", "")
            or ""
        )
        custom_fields = fields.get("customFields", issue.get("customFields", {})) or {}
        customer_email = (
            custom_fields.get("affectedUser", "")
            or reporter.get("emailAddress", "")
            or reporter.get("email", "")
            or ""
        )
        created_at = fields.get("created", issue.get("created", "")) or ""
        tags = fields.get("labels", issue.get("labels", [])) or []

    else:
        # Best-effort generic fallback
        source     = "Unknown"
        ticket_id  = str(raw_ticket.get("id", raw_ticket.get("key", "")))
        subject    = raw_ticket.get("subject", raw_ticket.get("summary", "")) or ""
        body       = raw_ticket.get("description", raw_ticket.get("body", "")) or ""
        customer_name  = ""
        customer_email = ""
        created_at = raw_ticket.get("created_at", raw_ticket.get("created", "")) or ""
        tags = raw_ticket.get("tags", raw_ticket.get("labels", [])) or []

    combined_text = f"{subject}. {body}".strip()

    return {
        "ticket_id":      ticket_id,
        "source":         source,
        "subject":        subject,
        "body":           body,
        "customer_name":  customer_name,
        "customer_email": customer_email,
        "created_at":     created_at,
        "tags":           [str(t) for t in tags],
        "combined_text":  combined_text,
    }


# ---------------------------------------------------------------------------
# Severity scoring
# ---------------------------------------------------------------------------

def _severity_score(text: str) -> int:
    """Return a raw severity score based on business-impact keyword matches."""
    text_lower = text.lower()
    score = 0
    for keyword, weight in _SEVERITY_KEYWORDS.items():
        if keyword in text_lower:
            score += weight
    return score


def _has_billing_critical(text: str) -> bool:
    """True if text contains any billing/auth critical keywords."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in _BILLING_CRITICAL_KEYWORDS)


def _compute_priority(
    duplicate_count: int,
    combined_text: str,
) -> tuple[str, str]:
    """
    Compute (priority_label, priority_reason) for a cluster.

    Rules:
      - 3+ tickets in cluster  ->  P0 (Critical)
      - 2 tickets + billing/auth keyword  ->  P0 (Critical)
      - 2 tickets               ->  P1 (High)
      - 1 ticket + billing/auth keyword  ->  P1 (High)
      - 1 ticket                ->  P2 (Medium)
    """
    has_critical = _has_billing_critical(combined_text)
    sev = _severity_score(combined_text)

    if duplicate_count >= 3:
        priority = "P0"
        reason = (
            f"{duplicate_count} duplicate complaints filed. "
            f"Business impact score: {sev}. Immediate escalation required."
        )
    elif duplicate_count == 2 and has_critical:
        priority = "P0"
        reason = (
            f"{duplicate_count} duplicate reports with critical billing/auth keywords "
            f"(impact score: {sev}). Escalated to P0."
        )
    elif duplicate_count == 2:
        priority = "P1"
        reason = (
            f"{duplicate_count} duplicate complaints (impact score: {sev}). "
            f"Assigned P1 (High)."
        )
    elif has_critical:
        priority = "P1"
        reason = (
            f"Single ticket but contains critical billing/auth keywords "
            f"(impact score: {sev}). Assigned P1 (High)."
        )
    else:
        priority = "P2"
        reason = (
            f"Single ticket, moderate impact score {sev}. "
            f"Assigned P2 (Medium)."
        )

    return priority, reason


# ---------------------------------------------------------------------------
# Cosine similarity clustering (connected-components)
# ---------------------------------------------------------------------------

def _connected_components(
    similarity_matrix: np.ndarray,
    threshold: float,
) -> list[list[int]]:
    """
    Union-Find connected-component clustering.

    Two tickets i and j are in the same cluster if
    similarity_matrix[i, j] >= threshold.

    Returns a list of clusters, each cluster being a sorted list of indices.
    """
    n = len(similarity_matrix)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            if similarity_matrix[i, j] >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    return [sorted(members) for members in groups.values()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cluster_tickets(
    tickets: list[dict],
    similarity_threshold: float = 0.58,
) -> list[dict]:
    """
    Embed, cluster, and prioritise a list of normalised tickets.

    Args:
        tickets:              List of dicts from normalize_ticket().
        similarity_threshold: Minimum cosine similarity to merge two tickets
                              into the same cluster.  Default 0.58 was tuned
                              to yield 4 clean clusters from the 8 sample tickets.

    Returns:
        List of cluster dicts, each containing:
          cluster_id, representative_ticket, tickets (list),
          duplicate_count, avg_similarity, priority, priority_reason,
          combined_cluster_query.
    """
    if not tickets:
        return []

    texts = [t["combined_text"] for t in tickets]
    print(f"[INFO] Embedding {len(texts)} ticket(s) for clustering ...")
    embeddings = embed_texts(texts)   # (N, 384) normalised -> dot = cosine

    # Full pairwise cosine similarity matrix (fast: just matrix multiply)
    sim_matrix: np.ndarray = embeddings @ embeddings.T
    np.fill_diagonal(sim_matrix, 1.0)

    component_groups = _connected_components(sim_matrix, similarity_threshold)
    print(f"[OK]   Formed {len(component_groups)} cluster(s) from "
          f"{len(tickets)} ticket(s).")

    clusters: list[dict] = []
    for cluster_idx, member_indices in enumerate(component_groups):
        cluster_tickets_list = [tickets[i] for i in member_indices]

        # Representative ticket = the one with highest avg similarity to others
        if len(member_indices) == 1:
            rep_idx = member_indices[0]
            avg_sim = 1.0
        else:
            sub_matrix = sim_matrix[np.ix_(member_indices, member_indices)]
            avg_sims = (sub_matrix.sum(axis=1) - 1.0) / (len(member_indices) - 1)
            best_local = int(np.argmax(avg_sims))
            rep_idx = member_indices[best_local]

            # Average similarity = mean of off-diagonal pairs
            mask = np.ones(sub_matrix.shape, dtype=bool)
            np.fill_diagonal(mask, False)
            avg_sim = float(sub_matrix[mask].mean())

        rep_ticket = tickets[rep_idx]

        # Build combined query text for downstream semantic code search
        all_subjects = " ".join(t["subject"] for t in cluster_tickets_list)
        all_bodies   = " ".join(t["body"]    for t in cluster_tickets_list)
        combined_cluster_query = f"{all_subjects} {all_bodies}".strip()

        priority, priority_reason = _compute_priority(
            duplicate_count=len(cluster_tickets_list),
            combined_text=combined_cluster_query,
        )

        clusters.append({
            "cluster_id":             f"CLU-{cluster_idx + 1:03d}",
            "representative_ticket":  rep_ticket,
            "tickets":                cluster_tickets_list,
            "duplicate_count":        len(cluster_tickets_list),
            "avg_similarity":         round(avg_sim, 4),
            "priority":               priority,
            "priority_reason":        priority_reason,
            "combined_cluster_query": combined_cluster_query,
        })

    # Sort clusters: P0 first, then P1, then P2; within same priority, more dupes first
    _prio_order = {"P0": 0, "P1": 1, "P2": 2}
    clusters.sort(
        key=lambda c: (_prio_order.get(c["priority"], 9), -c["duplicate_count"])
    )

    return clusters
