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
        address: A dict representing the customer's shipping address.

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
