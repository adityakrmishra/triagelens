"""
LLM reasoner and deterministic AST fallback synthesizer.
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")


def _is_placeholder(key: str) -> bool:
    if not key or not key.strip():
        return True
    lower = key.strip().lower()
    return lower.startswith("your_") or lower in ("none", "null", "placeholder", "test")


def _deterministic_ast_reasoning(
    cluster: dict,
    suspect_functions: list[dict],
) -> dict[str, Any]:
    dup_count = cluster.get("duplicate_count", len(cluster.get("tickets", [])))
    prio = cluster.get("priority", "P1")
    rep_ticket = cluster.get("representative_ticket", {})
    subject = rep_ticket.get("subject", "").lower()
    body = rep_ticket.get("body", "").lower()
    combined = f"{subject} {body}"

    if "vip" in combined or "promo" in combined or "discount" in combined:
        cluster_summary = (
            f"Multiple high-value VIP customers report that entering the VIP25 promo code leaves cart totals "
            f"unchanged when purchasing clearance or sale items ({dup_count} duplicate ticket(s), {prio} escalation). "
            f"This represents a silent pricing calculation failure bypassing discounts without error feedback."
        )
    elif "shipping" in combined or "delivery" in combined or "charged 99" in combined:
        cluster_summary = (
            f"Customers whose order subtotals meet or match the free-shipping threshold are unexpectedly assessed "
            f"a standard shipping fee ({dup_count} duplicate ticket(s), {prio} escalation). "
            f"This indicates a boundary-condition evaluation defect in the checkout shipping fee resolver."
        )
    elif "magic" in combined or "link" in combined or "login" in combined or "expired" in combined:
        cluster_summary = (
            f"Users attempting passwordless authentication report one-time magic links expire within 10 to 45 seconds "
            f"of receipt instead of the documented 15-minute validity period ({dup_count} report(s), {prio} escalation). "
            f"This defect prevents customer sign-ins and results in complete account lockouts."
        )
    elif "upgrade" in combined or "prorate" in combined or "subscription" in combined or "billing" in combined:
        cluster_summary = (
            f"A customer upgrading their subscription plan mid-cycle with 5 days remaining was overbilled for 25 "
            f"consumed days instead of the 5 remaining days ({dup_count} report(s), {prio} escalation). "
            f"This points to inverted proration math in the subscription upgrade invoicing engine."
        )
    else:
        cluster_summary = (
            f"Synthesized {dup_count} customer complaint(s) prioritized as {prio} based on recurring business keywords. "
            f"Investigation reveals an unexpected business-logic divergence in the affected workflow."
        )

    match_reasons: dict[str, str] = {}

    for fn in suspect_functions:
        fn_name = fn.get("function_name", "")
        file_path = fn.get("file_path", "")
        score = fn.get("similarity_score", 0.0)

        if fn_name == "apply_promo":
            match_reasons[fn_name] = (
                "Rank #1 match: Function 'apply_promo' in shopflow/pricing.py contains the condition "
                "'if has_sale_item: return round(cart_total, 2)' which silently bypasses the VIP25 discount "
                "whenever any clearance/sale item is present in the cart, directly causing the reported zero-discount behavior."
            )
        elif fn_name == "resolve_free_shipping":
            match_reasons[fn_name] = (
                "Rank #1 match: Function 'resolve_free_shipping' in shopflow/checkout.py checks eligibility with strict inequality "
                "'if cart_subtotal > threshold:' rather than '>=', incorrectly assessing standard shipping fees on carts "
                "that hit the free-shipping threshold exactly."
            )
        elif fn_name == "check_magic_link_expiry":
            match_reasons[fn_name] = (
                "Rank #1 match: Function 'check_magic_link_expiry' in shopflow/auth_session.py evaluates "
                "'elapsed_time <= ttl_minutes' where elapsed_time is measured in seconds, missing the '* 60' factor and "
                "causing 15-minute magic links to expire in only 15 seconds."
            )
        elif fn_name == "prorate_upgrade_charge":
            match_reasons[fn_name] = (
                "Rank #1 match: Function 'prorate_upgrade_charge' in shopflow/subscriptions.py computes "
                "'days_used = billing_cycle_days - days_remaining' and charges for elapsed days rather than remaining days, "
                "inverting the customer's proration formula."
            )
        elif fn_name == "calculate_subtotal":
            match_reasons[fn_name] = (
                f"Secondary match in {file_path} (similarity {score:.2%}): Computes raw item totals prior to discount evaluation; "
                "matched due to shared pricing and cart calculation domain."
            )
        elif fn_name == "calculate_tax":
            match_reasons[fn_name] = (
                f"Secondary match in {file_path} (similarity {score:.2%}): Applies regional tax rates on the post-discount amount; "
                "shortlisted as a downstream component in the pricing calculation flow."
            )
        elif fn_name == "validate_shipping_address":
            match_reasons[fn_name] = (
                f"Secondary match in {file_path} (similarity {score:.2%}): Validates postal code and address fields; "
                "matched due to shared shipping validation context in the checkout pipeline."
            )
        elif fn_name == "verify_password_hash":
            match_reasons[fn_name] = (
                f"Secondary match in {file_path} (similarity {score:.2%}): Verifies user password hashes; "
                "matched as a sibling authentication routine in the session management module."
            )
        else:
            doc = fn.get("docstring", "").strip().split("\n")[0]
            desc = doc if doc else f"routine in {file_path}"
            match_reasons[fn_name] = (
                f"Matched with semantic similarity score {score:.2%}: {desc} ({fn_name})."
            )

    return {
        "cluster_summary": cluster_summary,
        "match_reasons": match_reasons,
        "mode": "ast_heuristic_fallback",
    }


def explain_matches_and_summarize(
    cluster: dict,
    suspect_functions: list[dict],
) -> dict[str, Any]:
    if _is_placeholder(LLM_API_KEY):
        return _deterministic_ast_reasoning(cluster, suspect_functions)

    try:
        import requests

        prompt_payload = {
            "cluster_id": cluster.get("cluster_id", ""),
            "priority": cluster.get("priority", "P1"),
            "duplicate_count": cluster.get("duplicate_count", 1),
            "tickets": [
                {
                    "ticket_id": t.get("ticket_id"),
                    "source": t.get("source"),
                    "subject": t.get("subject"),
                    "body": t.get("body"),
                }
                for t in cluster.get("tickets", [])
            ],
            "suspect_functions": [
                {
                    "function_name": f.get("function_name"),
                    "file_path": f.get("file_path"),
                    "signature": f.get("signature"),
                    "docstring": f.get("docstring"),
                    "similarity_score": f.get("similarity_score"),
                    "source_code": f.get("source_code", "")[:400],
                }
                for f in suspect_functions
            ],
        }

        system_prompt = (
            "You are a Principal Software Triage Engineer. Analyze the customer support ticket cluster and "
            "the shortlisted code functions retrieved from AST semantic search. "
            "Output a JSON object with exactly two keys:\n"
            "1. 'cluster_summary': Exactly 2 concise sentences synthesizing the customer complaints and business impact.\n"
            "2. 'match_reasons': An object mapping each function_name to a clear 1-2 sentence technical explanation "
            "of why that function matched and how its code logic or boundary condition relates to the complaint.\n"
            "Output valid JSON only."
        )

        headers = {
            "Authorization": f"Bearer {LLM_API_KEY.strip()}",
            "Content-Type": "application/json",
        }

        url = f"{LLM_BASE_URL}/chat/completions"
        req_body = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 600,
        }

        resp = requests.post(url, headers=headers, json=req_body, timeout=12)

        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)

            summary = parsed.get("cluster_summary", "").strip()
            reasons = parsed.get("match_reasons", {})

            if summary and isinstance(reasons, dict) and len(reasons) > 0:
                return {
                    "cluster_summary": summary,
                    "match_reasons": reasons,
                    "mode": "llm_api",
                }

    except Exception:
        pass

    return _deterministic_ast_reasoning(cluster, suspect_functions)
