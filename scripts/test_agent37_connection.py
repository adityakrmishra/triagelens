"""
scripts/test_agent37_connection.py
----------------------------------
Validates live Agent37 Cloud Sandbox connectivity:
  1. Verifies API key authentication against https://api.agent37.com/v1/members
  2. Checks workspace billing status at https://api.agent37.com/v1/billing
  3. Lists active instances at https://api.agent37.com/v1/instances
  4. Attempts instance provisioning if none exist
  5. Tests execution and responses endpoints on Agent37 edge gateway
"""

import os
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

API_KEY = os.getenv("AGENT37_API_KEY", "").strip()
BASE_URL = os.getenv("AGENT37_BASE_URL", "https://api.agent37.com").rstrip("/")
INSTANCE_ID = os.getenv("AGENT37_INSTANCE_ID", "agent37-sandbox-demo-01").strip()

if not API_KEY or API_KEY.startswith("your_"):
    print("[ERROR] AGENT37_API_KEY is missing or set to placeholder in .env")
    sys.exit(1)

HEADERS_BEARER = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}
HEADERS_XKEY = {
    "X-Agent37-Key": API_KEY,
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

print("=" * 70)
print("  Agent37 Live Cloud Sandbox Connectivity Probe")
print("=" * 70)

# 1. Probe Members / Account Verification
print("\n[STEP 1] Probing Account Authentication (GET /v1/members) ...")
try:
    r_mem = requests.get(f"{BASE_URL}/v1/members", headers=HEADERS_BEARER, timeout=10)
    print(f"  Status: {r_mem.status_code}")
    if r_mem.status_code == 200:
        data = r_mem.json()
        members = data.get("members", [])
        if members:
            email = members[0].get("email", "unknown")
            role = members[0].get("role", "unknown")
            print(f"  [OK] Authenticated as: {email} (role: {role})")
        else:
            print("  [OK] Authenticated (0 members listed)")
    else:
        print(f"  [WARN] Response: {r_mem.text[:200]}")
except Exception as exc:
    print(f"  [ERROR] {exc}")

# 2. Probe Billing & Wallet Status
print("\n[STEP 2] Probing Workspace Balance (GET /v1/billing) ...")
try:
    r_bill = requests.get(f"{BASE_URL}/v1/billing", headers=HEADERS_BEARER, timeout=10)
    print(f"  Status: {r_bill.status_code}")
    if r_bill.status_code == 200:
        bdata = r_bill.json()
        balance = bdata.get("balance_usd", 0)
        tier = bdata.get("tier", 0)
        limit = bdata.get("instance_limit", 1)
        print(f"  [OK] Balance: ${balance:.4f} USD | Tier: {tier} | Instance Limit: {limit}")
    else:
        print(f"  [WARN] Response: {r_bill.text[:200]}")
except Exception as exc:
    print(f"  [ERROR] {exc}")

# 3. List Active Instances
print("\n[STEP 3] Listing Workspace Instances (GET /v1/instances) ...")
active_instance_id = None
try:
    r_inst = requests.get(f"{BASE_URL}/v1/instances", headers=HEADERS_BEARER, timeout=10)
    print(f"  Status: {r_inst.status_code}")
    if r_inst.status_code == 200:
        instances = r_inst.json().get("data", [])
        print(f"  [OK] Found {len(instances)} instance(s) in workspace.")
        for idx, inst in enumerate(instances, 1):
            iid = inst.get("id")
            iname = inst.get("name")
            istatus = inst.get("status")
            print(f"    #{idx}: ID={iid} | Name={iname} | Status={istatus}")
            if idx == 1:
                active_instance_id = iid
    else:
        print(f"  [WARN] Response: {r_inst.text[:200]}")
except Exception as exc:
    print(f"  [ERROR] {exc}")

# 4. Provision Instance if None Exists
if not active_instance_id:
    print("\n[STEP 4] Attempting to Provision Instance (POST /v1/instances) ...")
    create_payload = {
        "template": "agent37-hermes",
        "name": "triagelens-sandbox",
        "auto_sleep": True,
        "idle_timeout_seconds": 900,
    }
    try:
        r_create = requests.post(f"{BASE_URL}/v1/instances", json=create_payload, headers=HEADERS_BEARER, timeout=15)
        print(f"  Status: {r_create.status_code}")
        if r_create.status_code in (200, 201):
            inst_obj = r_create.json().get("data", r_create.json())
            active_instance_id = inst_obj.get("id")
            print(f"  [OK] Instance Created Successfully! ID: {active_instance_id}")
        else:
            err_msg = r_create.json().get("error", {}).get("message", r_create.text[:200])
            print(f"  [INFO] Agent37 Response: {err_msg}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")

# Target instance ID for testing
target_id = active_instance_id or INSTANCE_ID
print(f"\n[STEP 5] Testing Edge Gateway with Target ID: {target_id}")

# 5a. Test Command Execution via Exec Endpoint
exec_url = f"https://sandbox.agent37.app/v1/instances/{target_id}/exec"
print(f"  -> Testing Exec: {exec_url}")
repro_cmd = "python -c \"from shopflow.pricing import apply_promo; res = apply_promo(2000.0, 'VIP25', True, True); assert res == 1500.0\""
try:
    r_exec = requests.post(
        exec_url,
        json={"command": repro_cmd, "user": "root"},
        headers=HEADERS_XKEY,
        timeout=10,
    )
    print(f"     Status: {r_exec.status_code} | Body: {r_exec.text[:150]}")
except Exception as exc:
    print(f"     Exec exception: {exc}")

# 5b. Test Agent Responses Endpoint
resp_url = f"https://{target_id}.agent37.app/v1/responses"
print(f"  -> Testing Responses: {resp_url}")
try:
    r_resp = requests.post(
        resp_url,
        json={
            "input": "Verify silent business logic bug in shopflow.pricing.apply_promo: VIP25 coupon on sale item returns 2000.0 instead of 1500.0.",
            "stream": False,
            "model": "default",
            "agent": "hermes",
        },
        headers=HEADERS_XKEY,
        timeout=10,
    )
    print(f"     Status: {r_resp.status_code} | Body: {r_resp.text[:150]}")
except Exception as exc:
    print(f"     Responses exception: {exc}")

print("\n" + "=" * 70)
print("  Agent37 Live Probe Completed")
print("=" * 70 + "\n")
