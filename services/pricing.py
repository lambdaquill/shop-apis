"""Pricing rules: discounts and tax."""
import config


def apply_discount(price: float, pct: float) -> float:
    """Apply a percentage discount (0-100) to a price."""
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")
    return round(price * (1 - pct / 100), 2)


def add_tax(amount: float) -> float:
    """Add TAX_RATE tax to an amount."""
    return round(amount * (1 + config.TAX_RATE), 2)
