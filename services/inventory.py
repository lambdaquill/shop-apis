"""Stock checks on top of the data store."""
import config
from db import store


def check_stock(product_id: int, qty: int) -> bool:
    """True if enough stock is available."""
    product = store.get_product(product_id)
    return bool(product) and product["stock"] >= qty


def is_low_stock(product_id: int) -> bool:
    """True if stock is at or below LOW_STOCK_THRESHOLD."""
    product = store.get_product(product_id)
    return bool(product) and product["stock"] <= config.LOW_STOCK_THRESHOLD
