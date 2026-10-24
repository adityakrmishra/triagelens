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
        region: ISO-3166-1 alpha-2 country/region code (e.g. ``'IN'``).

    Returns:
        The tax amount rounded to 2 decimal places.
    """
    rate = TAX_RATES.get(region.upper(), DEFAULT_TAX_RATE)
    return round(amount * rate, 2)
