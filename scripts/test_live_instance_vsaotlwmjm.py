"""
scripts/test_live_instance_vsaotlwmjm.py
----------------------------------------
Executes live remote verification on Agent37 Cloud Container `vsaotlwmjm`:
  1. Queries GET https://api.agent37.com/v1/instances to inspect live instance vsaotlwmjm.
  2. Dispatches Python silent-bug execution commands to POST https://sandbox.agent37.app/v1/instances/vsaotlwmjm/exec.
  3. Dispatches verification prompts to POST https://vsaotlwmjm.agent37.app/v1/responses.
"""

from __future__ import annotations

import os
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)

API_KEY = os.getenv("AGENT37_API_KEY", "").strip()
BASE_URL = os.getenv("AGENT37_BASE_URL", "https://api.agent37.com").rstrip("/")
INSTANCE_ID = os.getenv("AGENT37_INSTANCE_ID", "vsaotlwmjm").strip()

if not API_KEY or API_KEY.startswith("your_"):
    print("[ERROR] AGENT37_API_KEY is missing or invalid in .env")
    sys.exit(1)

HEADERS = {
    "X-Agent37-Key": API_KEY,
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

print("=" * 76)
print(f"  Agent37 Live Cloud Sandbox Verification: Container [{INSTANCE_ID}]")
print("=" * 76)

# 1. Inspect live instance
print(f"\n[1/3] Inspecting Live Instances at {BASE_URL}/v1/instances ...")
try:
    r_inst = requests.get(f"{BASE_URL}/v1/instances", headers=HEADERS, timeout=12)
    print(f"  Status Code: {r_inst.status_code}")
    if r_inst.status_code == 200:
        data = r_inst.json().get("data", [])
        print(f"  Found {len(data)} instance(s) in workspace:")
        for inst in data:
            iid = inst.get("id")
            iname = inst.get("name")
            istatus = inst.get("status")
            print(f"    - ID: {iid} | Name: {iname} | Status: {istatus}")
    else:
        print(f"  Response: {r_inst.text[:200]}")
except Exception as exc:
    print(f"  [ERROR] Instance inspection error: {exc}")

# 2. Execute Python silent bug tests on POST https://sandbox.agent37.app/v1/instances/{id}/exec
exec_url = f"https://sandbox.agent37.app/v1/instances/{INSTANCE_ID}/exec"
print(f"\n[2/3] Executing Silent-Bug Repros on Remote Container ({exec_url}) ...")

test_cases = [
    (
        "CLU-001 (apply_promo)",
        "python3 -c \"def apply_promo(cart_total, promo_code, has_sale_item=False, is_vip=False):\n"
        "    if has_sale_item:\n"
        "        return round(cart_total, 2)\n"
        "    return round(cart_total * 0.75, 2)\n\n"
        "res = apply_promo(2000.0, 'VIP25', True, True)\n"
        "assert res == 1500.0, f'VIP25 bug reproduced: got {res} expected 1500.0'\n\""
    ),
    (
        "CLU-002 (resolve_free_shipping)",
        "python3 -c \"def resolve_free_shipping(cart_subtotal, threshold=1000.0):\n"
        "    if cart_subtotal > threshold:\n"
        "        return 0.0\n"
        "    return 99.0\n\n"
        "fee = resolve_free_shipping(1000.0, 1000.0)\n"
        "assert fee == 0.0, f'Free shipping bug reproduced: fee {fee} charged on exact threshold'\n\""
    ),
    (
        "CLU-003 (check_magic_link_expiry)",
        "python3 -c \"def check_magic_link_expiry(issued_at, current, ttl_min=15):\n"
        "    elapsed = current - issued_at\n"
        "    return elapsed <= ttl_min\n\n"
        "valid = check_magic_link_expiry(1000000, 1000030, 15)\n"
        "assert valid is True, 'Magic link bug reproduced: 30s elapsed flagged expired on 15m TTL'\n\""
    ),
    (
        "CLU-004 (prorate_upgrade_charge)",
        "python3 -c \"def prorate_upgrade_charge(cur, new, days_rem, cycle=30):\n"
        "    diff = new - cur\n"
        "    daily = diff / cycle\n"
        "    days_used = cycle - days_rem\n"
        "    return round(daily * days_used, 2)\n\n"
        "c = prorate_upgrade_charge(500.0, 1000.0, 5, 30)\n"
        "assert c == 83.33, f'Proration bug reproduced: charged {c} instead of 83.33'\n\""
    ),
]

exec_results = []
for label, cmd in test_cases:
    print(f"\n  -> Running: {label}")
    try:
        r_exec = requests.post(
            exec_url,
            json={"command": cmd, "user": "root"},
            headers=HEADERS,
            timeout=15,
        )
        print(f"     Status: {r_exec.status_code}")
        print(f"     Container Response: {r_exec.text[:200]}")
        exec_results.append({
            "label": label,
            "status_code": r_exec.status_code,
            "body": r_exec.text,
        })
    except Exception as exc:
        print(f"     [ERROR] Exec failed: {exc}")
        exec_results.append({
            "label": label,
            "status_code": 0,
            "error": str(exc),
        })

# Also run scripts/agent37_cloud_entry.py inside the container!
print("\n  -> Uploading and Running scripts/agent37_cloud_entry.py inside container ...")
entry_code = (PROJECT_ROOT / "scripts" / "agent37_cloud_entry.py").read_text(encoding="utf-8", errors="replace")
# Base64 encode to safely write into container without escaping issues
import base64
b64_code = base64.b64encode(entry_code.encode("utf-8")).decode("ascii")
setup_cmd = f"echo '{b64_code}' | base64 -d > /tmp/agent37_cloud_entry.py && python3 /tmp/agent37_cloud_entry.py"
try:
    r_entry = requests.post(
        exec_url,
        json={"command": setup_cmd, "user": "root"},
        headers=HEADERS,
        timeout=25,
    )
    print(f"     Entry Run Status: {r_entry.status_code}")
    print(f"     Entry Run Output Preview:\n{r_entry.text[:400]}")
except Exception as exc:
    print(f"     [ERROR] Cloud entry execution failed: {exc}")

# 3. Call Responses endpoint on POST https://{id}.agent37.app/v1/responses
resp_url = f"https://{INSTANCE_ID}.agent37.app/v1/responses"
print(f"\n[3/3] Sending Verification Prompt to Live Agent ({resp_url}) ...")
prompt = (
    "TriageLens silent bug verification for CLU-001 apply_promo(2000.0, 'VIP25', has_sale_item=True, is_vip=True) "
    "-> returned 2000.0 instead of 1500.0. Confirm bug reproduction."
)
try:
    r_resp = requests.post(
        resp_url,
        json={
            "input": prompt,
            "stream": False,
            "model": "default",
            "agent": "hermes",
        },
        headers=HEADERS,
        timeout=45,
    )
    print(f"  Status Code: {r_resp.status_code}")
    print(f"  Body Preview: {r_resp.text[:300]}")
except Exception as exc:
    print(f"  [ERROR] Responses failed: {exc}")

print("\n" + "=" * 76)
print(f"  Agent37 Live Container [{INSTANCE_ID}] Probe Complete!")
print("=" * 76 + "\n")
