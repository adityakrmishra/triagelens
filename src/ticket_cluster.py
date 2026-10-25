"""
Ticket normalization, embedding, and connected-component clustering.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from src.embedder import embed_texts

_SEVERITY_KEYWORDS: dict[str, int] = {
    "payment": 4,
    "charged": 4,
    "billing": 4,
    "invoice": 4,
    "refund": 4,
    "overcharge": 4,
    "prorated": 3,
    "prorate": 3,
    "upgrade": 2,
    "checkout": 3,
    "cart": 2,
    "shipping": 3,
    "delivery": 2,
    "free shipping": 3,
    "discount": 3,
    "promo": 3,
    "coupon": 3,
    "vip": 3,
    "vip25": 3,
    "clearance": 2,
    "sale": 2,
    "login": 3,
    "magic link": 4,
    "magic-link": 4,
    "expired": 3,
    "expiry": 3,
    "locked out": 4,
    "lockout": 4,
    "password": 2,
    "urgent": 3,
    "critical": 3,
    "broken": 2,
    "not working": 2,
    "bug": 2,
}

_BILLING_CRITICAL_KEYWORDS = {
    "payment", "charged", "billing", "invoice", "refund",
    "overcharge", "prorated", "prorate", "magic link", "magic-link",
    "locked out", "lockout",
}


def _is_zendesk(raw: dict) -> bool:
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
    if raw.get("_schema") == "jira_v3":
        return True
    if "issue" in raw and isinstance(raw["issue"], dict):
        return True
    return (
        "summary" in raw
        or "issueType" in raw
        or "key" in raw
        or ("fields" in raw and "summary" in raw.get("fields", {}))
        or (isinstance(raw.get("id", ""), str) and "-" in str(raw.get("id", "")))
    )


def parse_tickets_payload(payload: dict | list) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        if "tickets" in payload and isinstance(payload["tickets"], list):
            return [t for t in payload["tickets"] if isinstance(t, dict)]
        if "issues" in payload and isinstance(payload["issues"], list):
            return [t for t in payload["issues"] if isinstance(t, dict)]
        return [payload]

    return []


def normalize_ticket(raw_ticket: dict) -> dict:
    if not isinstance(raw_ticket, dict):
        raise TypeError(f"raw_ticket must be a dict, got {type(raw_ticket).__name__}")

    raw = raw_ticket.get("ticket", raw_ticket)

    if _is_zendesk(raw_ticket):
        schema = "zendesk_v2"
        ticket_id = str(raw.get("id") or raw.get("ticket_id") or "ZD-UNKNOWN")
        subject = str(raw.get("subject") or "").strip()

        desc = raw.get("description") or raw.get("body") or ""
        if isinstance(desc, dict):
            body = str(desc.get("body") or desc.get("plain_body") or "").strip()
        else:
            body = str(desc).strip()

        customer_name = ""
        customer_email = ""
        requester = raw.get("requester")
        if isinstance(requester, dict):
            customer_name = str(requester.get("name") or "").strip()
            customer_email = str(requester.get("email") or "").strip()

        raw_tags = raw.get("tags") or []
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        created_at = str(raw.get("created_at") or "")

    elif _is_jira(raw_ticket):
        schema = "jira_v3"
        fields = raw.get("fields", {}) if isinstance(raw.get("fields"), dict) else {}

        raw_id = raw.get("key") or raw.get("id") or fields.get("key") or "JIRA-UNKNOWN"
        ticket_id = str(raw_id)
        subject = str(fields.get("summary") or raw.get("summary") or "").strip()

        desc_raw = fields.get("description") or raw.get("description") or ""
        if isinstance(desc_raw, dict):
            body_parts: list[str] = []
            for block in desc_raw.get("content", []):
                for inline in block.get("content", []):
                    text = inline.get("text", "")
                    if text:
                        body_parts.append(text)
            body = " ".join(body_parts).strip()
        else:
            body = str(desc_raw).strip()

        customer_name = ""
        customer_email = ""
        reporter = fields.get("reporter") or raw.get("reporter")
        if isinstance(reporter, dict):
            customer_name = str(
                reporter.get("displayName") or reporter.get("name") or ""
            ).strip()
            customer_email = str(reporter.get("emailAddress") or "").strip()

        raw_labels = fields.get("labels") or raw.get("labels") or []
        tags = [str(lbl).strip() for lbl in raw_labels if str(lbl).strip()]
        created_at = str(fields.get("created") or raw.get("created_at") or "")

    else:
        schema = "generic"
        ticket_id = str(
            raw.get("id") or raw.get("ticket_id") or raw.get("key") or "UNKNOWN"
        )
        subject = str(raw.get("subject") or raw.get("summary") or raw.get("title") or "").strip()
        body = str(
            raw.get("body")
            or raw.get("description")
            or raw.get("text")
            or raw.get("details")
            or ""
        ).strip()
        customer_name = str(raw.get("customer_name") or raw.get("user") or "").strip()
        customer_email = str(raw.get("customer_email") or raw.get("email") or "").strip()
        raw_tags = raw.get("tags") or raw.get("labels") or []
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
        created_at = str(raw.get("created_at") or "")

    tag_str = " ".join(tags)
    combined_parts = [p for p in (subject, body, tag_str) if p]
    combined_text = " ".join(combined_parts).strip()

    return {
        "ticket_id": ticket_id,
        "source": schema,
        "subject": subject,
        "body": body,
        "customer_name": customer_name,
        "customer_email": customer_email,
        "tags": tags,
        "created_at": created_at,
        "combined_text": combined_text,
        "raw_payload": raw_ticket,
    }


def _severity_score(text: str) -> int:
    text_lower = text.lower()
    score = 0
    for keyword, weight in _SEVERITY_KEYWORDS.items():
        if keyword in text_lower:
            score += weight
    return score


def _has_billing_critical(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in _BILLING_CRITICAL_KEYWORDS)


def _compute_priority(
    duplicate_count: int,
    combined_text: str,
) -> tuple[str, str]:
    has_critical = _has_billing_critical(combined_text)
    sev = _severity_score(combined_text)

    if duplicate_count >= 3:
        priority = "P0"
        reason = f"{duplicate_count} duplicate complaints filed. Impact score: {sev}."
    elif duplicate_count == 2 and has_critical:
        priority = "P0"
        reason = f"{duplicate_count} duplicate reports with critical billing/auth keywords. Impact score: {sev}."
    elif duplicate_count == 2:
        priority = "P1"
        reason = f"{duplicate_count} duplicate complaints. Impact score: {sev}."
    elif has_critical:
        priority = "P1"
        reason = f"Single report with critical billing/auth keywords. Impact score: {sev}."
    else:
        priority = "P2"
        reason = f"Single report, impact score {sev}."

    return priority, reason


def _connected_components(
    similarity_matrix: np.ndarray,
    threshold: float,
) -> list[list[int]]:
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


def cluster_tickets(
    tickets: list[dict],
    similarity_threshold: float = 0.58,
) -> list[dict]:
    if not tickets:
        return []

    texts = [t["combined_text"] for t in tickets]
    embeddings = embed_texts(texts)

    sim_matrix: np.ndarray = embeddings @ embeddings.T
    np.fill_diagonal(sim_matrix, 1.0)

    # Cosine threshold 0.58 tuned for connected-component clustering across support complaint domains
    component_groups = _connected_components(sim_matrix, similarity_threshold)

    clusters: list[dict] = []
    for cluster_idx, member_indices in enumerate(component_groups):
        cluster_tickets_list = [tickets[i] for i in member_indices]

        if len(member_indices) == 1:
            rep_idx = member_indices[0]
            avg_sim = 1.0
        else:
            sub_matrix = sim_matrix[np.ix_(member_indices, member_indices)]
            avg_sims = (sub_matrix.sum(axis=1) - 1.0) / (len(member_indices) - 1)
            best_local = int(np.argmax(avg_sims))
            rep_idx = member_indices[best_local]

            mask = np.ones(sub_matrix.shape, dtype=bool)
            np.fill_diagonal(mask, False)
            avg_sim = float(sub_matrix[mask].mean())

        rep_ticket = tickets[rep_idx]
        all_subjects = " ".join(t["subject"] for t in cluster_tickets_list)
        all_bodies = " ".join(t["body"] for t in cluster_tickets_list)
        combined_cluster_query = f"{all_subjects} {all_bodies}".strip()

        priority, priority_reason = _compute_priority(
            duplicate_count=len(cluster_tickets_list),
            combined_text=combined_cluster_query,
        )

        clusters.append({
            "cluster_id": f"CLU-{cluster_idx + 1:03d}",
            "representative_ticket": rep_ticket,
            "tickets": cluster_tickets_list,
            "duplicate_count": len(cluster_tickets_list),
            "avg_similarity": round(avg_sim, 4),
            "priority": priority,
            "priority_reason": priority_reason,
            "combined_cluster_query": combined_cluster_query,
        })

    _prio_order = {"P0": 0, "P1": 1, "P2": 2}
    clusters.sort(
        key=lambda c: (_prio_order.get(c["priority"], 9), -c["duplicate_count"])
    )

    return clusters
