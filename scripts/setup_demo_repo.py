"""
scripts/setup_demo_repo.py
------------------------------------------------------------------------------
TriageLens · Phase 1 · Demo Repository Generator

Creates (or fully resets) `demo_target_repo/` as a real Git repository with
four realistic Python modules inside `shopflow/`, each committed by a
different simulated developer identity so that `git blame` reliably attributes
every function to its "author".

Planted silent bugs (no exceptions raised – pure logic errors):
  Bug #1  pricing.py        apply_promo       → VIP25 silently skipped on
                                                 carts with sale items
  Bug #2  checkout.py       resolve_free_shipping → strict > instead of >=
                                                     (off-by-one on threshold)
  Bug #3  auth_session.py   check_magic_link_expiry → compares raw seconds to
                                                        ttl_minutes (missing ×60)
  Bug #4  subscriptions.py  prorate_upgrade_charge  → charges days *used*
                                                       instead of days *remaining*

Usage
-----
    python scripts/setup_demo_repo.py

After running, verify git blame with:
    cd demo_target_repo
    git blame shopflow/pricing.py
    git blame shopflow/checkout.py
    git blame shopflow/auth_session.py
    git blame shopflow/subscriptions.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

# -- Paths ----------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent                         # project root
REPO_DIR = ROOT / "demo_target_repo"
SHOPFLOW = REPO_DIR / "shopflow"


# -- Helpers --------------------------------------------------------------------

def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    """Run a subprocess command, streaming output. Raises on non-zero exit."""
    result = subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}"
        )


def git(*args: str, author_name: str = "", author_email: str = "", cwd: Path | None = None) -> None:
    """Thin wrapper around `git`, optionally overriding author identity."""
    cmd = ["git"]
    if author_name:
        cmd += ["-c", f"user.name={author_name}"]
    if author_email:
        cmd += ["-c", f"user.email={author_email}"]
    cmd += list(args)
    run(cmd, cwd=cwd or ROOT)


def write_file(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8", errors="replace")
    print(f"  [write] {path.relative_to(ROOT)}")


def stage_and_commit(
    rel_paths: list[str],
    message: str,
    author_name: str,
    author_email: str,
) -> None:
    """Stage specific files and commit into ROOT with a given author identity."""
    for p in rel_paths:
        git("add", p, cwd=ROOT)

    # Check if anything is staged for these paths
    status = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", *rel_paths],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()

    if status:
        git(
            "commit",
            "-m", message,
            "--author", f"{author_name} <{author_email}>",
            author_name=author_name,
            author_email=author_email,
            cwd=ROOT,
        )
        print(f"  [commit] {message!r} by {author_name}")
    else:
        print(f"  [ok] Clean: {', '.join(rel_paths)} already committed")


# -- Module source code ---------------------------------------------------------

PRICING_PY = '''\
"""
shopflow/pricing.py
Handles subtotal calculation, promotional discounts, and tax computation.

Owner: Aarav Sharma (@aarav-payments) <aarav@shopflow.io>
"""

from __future__ import annotations


# --- Subtotal -----------------------------------------------------------------

def calculate_subtotal(items: list[dict]) -> float:
    """
    Sum the (price * quantity) for every item in the cart.

    Args:
        items: List of dicts, each with keys ``price`` (float) and
               ``quantity`` (int).

    Returns:
        The raw cart subtotal before discounts or tax.
    """
    subtotal: float = 0.0
    for item in items:
        subtotal += float(item["price"]) * int(item.get("quantity", 1))
    return round(subtotal, 2)


# --- Promo / discount ---------------------------------------------------------

# Supported promo codes and their discount percentages
PROMO_TABLE: dict[str, float] = {
    "SAVE20": 0.20,
    "VIP25":  0.25,
    "FIRST10": 0.10,
    "SUMMER15": 0.15,
}


def apply_promo(
    cart_total: float,
    promo_code: str,
    has_sale_item: bool = False,
    is_vip: bool = False,
) -> float:
    """
    Applies promotional discount codes (e.g., SAVE20, VIP25) to the customer
    cart total and returns the discounted price.

    VIP members holding a VIP25 code receive 25 % off their entire cart,
    including carts that contain clearance or sale items.

    Args:
        cart_total:    Pre-discount cart total in INR.
        promo_code:    Uppercase promo code string entered by the customer.
        has_sale_item: True when the cart contains at least one sale / clearance
                       item.
        is_vip:        True when the customer account is flagged as VIP tier.

    Returns:
        The discounted cart total (float, rounded to 2 dp).
        Returns ``cart_total`` unchanged if the promo code is not recognised.
    """
    code = promo_code.strip().upper()
    discount_rate = PROMO_TABLE.get(code)

    if discount_rate is None:
        # Unrecognised code — return total unchanged
        return round(cart_total, 2)

    # -- BUG #1 (PLANTED — DO NOT FIX) ----------------------------------------
    # When the cart contains a sale item we bail out early, which silently
    # prevents VIP25 (and all other codes) from being applied — even though
    # VIP members are contractually entitled to their 25 % discount on *all*
    # carts.  No exception is raised, so the UI shows "Promo applied" while
    # the total remains unchanged.
    if has_sale_item:
        return round(cart_total, 2)          # ← silent wrong early-return
    # -- END BUG #1 ------------------------------------------------------------

    return round(cart_total * (1.0 - discount_rate), 2)


# --- Tax ----------------------------------------------------------------------

# Region-level GST / VAT rates
TAX_RATES: dict[str, float] = {
    "IN":  0.18,   # India GST
    "US":  0.08,   # blended US sales tax approximation
    "EU":  0.20,   # EU VAT
    "GB":  0.20,   # UK VAT
    "AU":  0.10,   # AU GST
}
DEFAULT_TAX_RATE = 0.18


def calculate_tax(amount: float, region: str) -> float:
    """
    Calculate the applicable tax for a given amount and region.

    Args:
        amount: The taxable amount (post-discount, pre-tax).
        region: ISO-3166-1 alpha-2 country/region code (e.g. ``\'IN\'``).

    Returns:
        The tax amount rounded to 2 decimal places.
    """
    rate = TAX_RATES.get(region.upper(), DEFAULT_TAX_RATE)
    return round(amount * rate, 2)
'''

CHECKOUT_PY = '''\
"""
shopflow/checkout.py
Handles shipping address validation and shipping fee resolution.

Owner: Priya Nair (@priya-checkout) <priya@shopflow.io>
"""

from __future__ import annotations


# --- Address validation -------------------------------------------------------

REQUIRED_ADDRESS_FIELDS = ("name", "line1", "city", "state", "pincode", "country")


def validate_shipping_address(address: dict) -> bool:
    """
    Verify that all mandatory address fields are present and non-empty.

    Args:
        address: A dict representing the customer\'s shipping address.

    Returns:
        True if the address is valid, False otherwise.
    """
    if not isinstance(address, dict):
        return False
    for field in REQUIRED_ADDRESS_FIELDS:
        value = address.get(field, "")
        if not str(value).strip():
            return False
    # Basic pincode sanity check (India: 6 digits)
    pincode = str(address.get("pincode", ""))
    if address.get("country", "").upper() == "IN" and not pincode.isdigit():
        return False
    return True


# --- Shipping fee -------------------------------------------------------------

FREE_SHIPPING_FEE = 0.0
FLAT_SHIPPING_FEE = 99.0


def resolve_free_shipping(
    cart_subtotal: float,
    cart_after_discount: float,
    threshold: float = 1000.0,
) -> float:
    """
    Determines shipping fee.  Orders reaching the threshold qualify for free
    shipping; otherwise charges 99.0 flat rate.

    Business rule: any order whose subtotal is ₹1,000 or above (i.e. >= 1000)
    ships for free, regardless of discounts applied afterwards.

    Args:
        cart_subtotal:      Raw cart subtotal before any promo discount.
        cart_after_discount: Cart total after promo discount has been applied.
        threshold:          Minimum subtotal to qualify for free shipping
                            (default ₹1,000).

    Returns:
        0.0 if the order qualifies for free shipping, else 99.0.
    """
    # -- BUG #2 (PLANTED — DO NOT FIX) ----------------------------------------
    # Two compounding errors:
    #   (a) Uses strict  >  instead of  >=, so an order of exactly ₹1,000
    #       is silently charged ₹99 instead of ₹0.
    #   (b) Checks `cart_subtotal` (pre-discount) rather than
    #       `cart_after_discount`, which may produce the wrong result when
    #       a large discount pushes the post-discount total below threshold.
    #       (Intentional: checking pre-discount IS the correct field per spec,
    #        but the > vs >= is the active silent bug for this demo.)
    if cart_subtotal > threshold:            # ← should be >= threshold
        return FREE_SHIPPING_FEE
    # -- END BUG #2 ------------------------------------------------------------

    return FLAT_SHIPPING_FEE
'''

AUTH_SESSION_PY = '''\
"""
shopflow/auth_session.py
Password verification and magic-link token expiry checks.

Owner: Rohan Verma (@rohan-auth) <rohan@shopflow.io>
"""

from __future__ import annotations

import hashlib
import hmac


# --- Password hashing ---------------------------------------------------------

def verify_password_hash(raw: str, hashed: str) -> bool:
    """
    Securely compare a raw plaintext password against a stored SHA-256 hex
    digest using constant-time comparison to prevent timing attacks.

    Args:
        raw:    The plaintext password supplied by the user at login.
        hashed: The stored SHA-256 hex digest from the database.

    Returns:
        True if the password matches, False otherwise.
    """
    raw_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(raw_digest, hashed)


# --- Magic-link TTL -----------------------------------------------------------

def check_magic_link_expiry(
    issued_at_epoch: int,
    current_epoch: int,
    ttl_minutes: int = 15,
) -> bool:
    """
    Validates whether a one-time login magic link token is still within its
    allowed time-to-live window.

    The link is considered valid when the elapsed time since issuance is
    strictly less than ``ttl_minutes`` minutes.

    Args:
        issued_at_epoch: Unix timestamp (seconds) at which the magic link was
                         generated and emailed to the user.
        current_epoch:   Current Unix timestamp (seconds) at the moment of
                         validation.
        ttl_minutes:     Maximum allowed age of the link in **minutes**
                         (default: 15).

    Returns:
        True  – the link is still valid (within TTL).
        False – the link has expired.
    """
    elapsed_seconds = current_epoch - issued_at_epoch

    # -- BUG #3 (PLANTED — DO NOT FIX) ----------------------------------------
    # `ttl_minutes` is compared directly against `elapsed_seconds` without
    # converting minutes → seconds (i.e., missing the  * 60  factor).
    # This means a 15-minute TTL silently becomes a 15-*second* TTL.
    # No error is raised; links simply expire almost immediately after
    # being issued, locking users out of their accounts.
    if elapsed_seconds < ttl_minutes:        # ← should be < (ttl_minutes * 60)
        return True   # link is valid
    # -- END BUG #3 ------------------------------------------------------------

    return False  # link has expired
'''

SUBSCRIPTIONS_PY = '''\
"""
shopflow/subscriptions.py
Subscription lifecycle helpers: proration, renewal, and plan management.

Owner: Sneha Patel (@sneha-billing) <sneha@shopflow.io>
"""

from __future__ import annotations


# --- Proration ----------------------------------------------------------------

def prorate_upgrade_charge(
    current_plan_price: float,
    new_plan_price: float,
    days_remaining: int,
    billing_cycle_days: int = 30,
) -> float:
    """
    Computes the prorated charge when a customer upgrades their subscription
    mid-cycle.

    The customer has already paid for the current plan.  When upgrading, they
    should only be charged the price *difference* for the **remaining** days in
    the billing cycle — not for days already consumed.

    Formula (correct):
        charge = (new_plan_price - current_plan_price)
                 * (days_remaining / billing_cycle_days)

    Args:
        current_plan_price:  Monthly price of the customer\'s current plan (INR).
        new_plan_price:      Monthly price of the plan being upgraded to (INR).
        days_remaining:      Days left in the current billing cycle at the time
                             of upgrade.
        billing_cycle_days:  Total days in the billing cycle (default 30).

    Returns:
        The prorated amount to charge immediately, in INR, rounded to 2 dp.
        Returns 0.0 if the new plan is not more expensive than the current plan.
    """
    price_diff = new_plan_price - current_plan_price
    if price_diff <= 0:
        return 0.0  # downgrade or same-plan switch — no immediate charge

    if billing_cycle_days <= 0:
        raise ValueError("billing_cycle_days must be a positive integer.")

    # -- BUG #4 (PLANTED — DO NOT FIX) ----------------------------------------
    # Uses `(billing_cycle_days - days_remaining) / billing_cycle_days` instead
    # of `days_remaining / billing_cycle_days`.
    # This charges the customer for the days *already used* (elapsed fraction)
    # rather than the days *remaining* (future fraction).
    # Example: 5 days remaining in a 30-day cycle:
    #   Correct  → charge for 5/30  of price_diff  (≈ 16.7 %)
    #   Buggy    → charge for 25/30 of price_diff  (≈ 83.3 %) ← 5× overcharge
    days_used = billing_cycle_days - days_remaining
    fraction = days_used / billing_cycle_days    # ← should be days_remaining / billing_cycle_days
    # -- END BUG #4 ------------------------------------------------------------

    return round(price_diff * fraction, 2)
'''

INIT_PY = '''\
"""shopflow — ShopFlow internal Python SDK."""

__version__ = "0.1.0"
__all__ = ["pricing", "checkout", "auth_session", "subscriptions"]
'''

README_MD = '''\
# ShopFlow Demo Target Repository

> **Internal use only — TriageLens demo fixture**

This repository is a synthetic but realistic Python codebase used as the
**analysis target** for the [TriageLens](https://github.com/yourorg/triagelens)
hackathon project.

It contains four intentionally planted **silent business-logic bugs** — no
stack traces, no exceptions — that TriageLens must discover solely from
plain-language customer support tickets.

## Module Map

| Module | Owner | Bug |
|---|---|---|
| `shopflow/pricing.py` | @aarav-payments | VIP25 silently skipped on carts with sale items |
| `shopflow/checkout.py` | @priya-checkout | Off-by-one on free-shipping threshold (> vs >=) |
| `shopflow/auth_session.py` | @rohan-auth | Magic-link TTL in seconds not minutes (missing ×60) |
| `shopflow/subscriptions.py` | @sneha-billing | Proration charges days *used* instead of days *remaining* |

## Running

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Git Blame

Each module was committed by its respective owner so that `git blame` maps
every function back to the responsible engineer:

```bash
git blame shopflow/pricing.py
git blame shopflow/checkout.py
git blame shopflow/auth_session.py
git blame shopflow/subscriptions.py
```
'''

GITIGNORE = '''\
__pycache__/
*.pyc
*.pyo
.pytest_cache/
*.egg-info/
dist/
build/
.env
'''

# -- Main setup routine ---------------------------------------------------------

# -- Main setup routine ---------------------------------------------------------

def cleanup_nested_git() -> None:
    """If a nested demo_target_repo/.git exists, remove it to prevent submodule trap."""
    nested_git = REPO_DIR / ".git"
    if nested_git.exists():
        print(f"[cleanup] Removing nested .git at {nested_git}")

        def handle_readonly(func, path, exc_info):
            import stat
            os.chmod(path, stat.S_IWRITE)
            func(path)

        if nested_git.is_dir():
            shutil.rmtree(nested_git, onerror=handle_readonly)
        else:
            nested_git.unlink()


def ensure_root_git() -> None:
    """Ensure project root is an initialized Git repository with .gitignore."""
    root_git = ROOT / ".git"
    if not root_git.exists():
        print("[init] Initializing root Git repository (branch: main)")
        run(["git", "init", "-b", "main"], cwd=ROOT)
        run(["git", "config", "user.name", "TriageLens Bot"], cwd=ROOT)
        run(["git", "config", "user.email", "bot@triagelens.io"], cwd=ROOT)

    gitignore_path = ROOT / ".gitignore"
    if not gitignore_path.exists():
        gitignore_content = (
            ".env\n"
            "__pycache__/\n"
            "*.pyc\n"
            "chroma_db/\n"
            ".pytest_cache/\n"
            "agent37_sandbox_run/\n"
        )
        gitignore_path.write_text(gitignore_content, encoding="utf-8", errors="replace")
        print("  [write] .gitignore created in root")


def commit_1_pricing() -> None:
    """Commit 1 - Aarav Sharma: pricing.py"""
    print("\n-- Commit 1: pricing.py (Aarav Sharma) --")
    SHOPFLOW.mkdir(parents=True, exist_ok=True)
    write_file(SHOPFLOW / "__init__.py", INIT_PY)
    write_file(SHOPFLOW / "pricing.py",  PRICING_PY)
    write_file(REPO_DIR / "README.md",   README_MD)
    stage_and_commit(
        ["demo_target_repo/shopflow/__init__.py", "demo_target_repo/shopflow/pricing.py", "demo_target_repo/README.md"],
        "feat(pricing): add subtotal, promo discount, and tax calculation",
        author_name="Aarav Sharma",
        author_email="aarav@shopflow.io",
    )


def commit_2_checkout() -> None:
    """Commit 2 - Priya Nair: checkout.py"""
    print("\n-- Commit 2: checkout.py (Priya Nair) --")
    write_file(SHOPFLOW / "checkout.py", CHECKOUT_PY)
    stage_and_commit(
        ["demo_target_repo/shopflow/checkout.py"],
        "feat(checkout): add address validation and shipping fee resolver",
        author_name="Priya Nair",
        author_email="priya@shopflow.io",
    )


def commit_3_auth() -> None:
    """Commit 3 - Rohan Verma: auth_session.py"""
    print("\n-- Commit 3: auth_session.py (Rohan Verma) --")
    write_file(SHOPFLOW / "auth_session.py", AUTH_SESSION_PY)
    stage_and_commit(
        ["demo_target_repo/shopflow/auth_session.py"],
        "feat(auth): add password hash verification and magic-link TTL check",
        author_name="Rohan Verma",
        author_email="rohan@shopflow.io",
    )


def commit_4_subscriptions() -> None:
    """Commit 4 - Sneha Patel: subscriptions.py"""
    print("\n-- Commit 4: subscriptions.py (Sneha Patel) --")
    write_file(SHOPFLOW / "subscriptions.py", SUBSCRIPTIONS_PY)
    stage_and_commit(
        ["demo_target_repo/shopflow/subscriptions.py"],
        "feat(subscriptions): add mid-cycle proration charge calculation",
        author_name="Sneha Patel",
        author_email="sneha@shopflow.io",
    )


def verify_blame() -> None:
    """Quick sanity-check: run git blame on pricing.py and print the output."""
    print("\n-- Verification: git blame demo_target_repo/shopflow/pricing.py --")
    result = subprocess.run(
        ["git", "blame", "--date=short", "-L", "41,45", "demo_target_repo/shopflow/pricing.py"],
        cwd=str(ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    safe_out = result.stdout.encode("ascii", errors="replace").decode("ascii")
    print(safe_out)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("git blame verification failed!")
    print("[OK] git blame works correctly on demo_target_repo/shopflow/pricing.py")


def print_summary() -> None:
    """Print a human-readable summary of what was created."""
    print("\n" + "=" * 60)
    print("  TriageLens - Demo Repo Setup Complete")
    print("=" * 60)
    print(f"\n  Location : {REPO_DIR}")
    print("\n  Commits  :")
    print("    1. Aarav Sharma  <aarav@shopflow.io>   pricing.py")
    print("    2. Priya Nair    <priya@shopflow.io>   checkout.py")
    print("    3. Rohan Verma   <rohan@shopflow.io>   auth_session.py")
    print("    4. Sneha Patel   <sneha@shopflow.io>   subscriptions.py")
    print("\n  Planted Bugs:")
    print("    #1 apply_promo()             -> VIP25 skipped on sale-item carts")
    print("    #2 resolve_free_shipping()   -> > instead of >= on threshold")
    print("    #3 check_magic_link_expiry() -> ttl_minutes not x60")
    print("    #4 prorate_upgrade_charge()  -> charges days used, not remaining")
    print("\n  Try it:")
    print("    git log --oneline demo_target_repo")
    print("    git blame demo_target_repo/shopflow/pricing.py")
    print("=" * 60 + "\n")


def setup_repository(repo_path: str | Path | None = None) -> None:
    """Exported setup entry point for app.py and CLI scripts."""
    cleanup_nested_git()
    ensure_root_git()
    commit_1_pricing()
    commit_2_checkout()
    commit_3_auth()
    commit_4_subscriptions()
    verify_blame()


# -- Entry point ----------------------------------------------------------------

def main() -> None:
    print("\n[TriageLens] Demo Repo Generator\n")
    setup_repository()
    print_summary()


if __name__ == "__main__":
    main()

