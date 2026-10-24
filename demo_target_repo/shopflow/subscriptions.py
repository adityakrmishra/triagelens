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
        current_plan_price:  Monthly price of the customer's current plan (INR).
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
