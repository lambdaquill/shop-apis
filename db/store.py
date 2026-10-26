"""In-memory data store for products and orders (swap for a real DB later)."""
from typing import Optional

PRODUCTS = {
    1: {"id": 1, "name": "Laptop", "price": 1000.0, "stock": 10},
    2: {"id": 2, "name": "Mouse", "price": 25.0, "stock": 100},
    3: {"id": 3, "name": "Keyboard", "price": 75.0, "stock": 3},
}
ORDERS: dict = {}


def list_products() -> list:
    """Return all products."""
    return list(PRODUCTS.values())


def get_product(product_id: int) -> Optional[dict]:
    """Return one product or None."""
    return PRODUCTS.get(product_id)


def reduce_stock(product_id: int, qty: int) -> None:
    """Decrease stock after a successful order."""
    PRODUCTS[product_id]["stock"] -= qty


def save_order(order: dict) -> dict:
    """Persist an order and assign it an id."""
    order["id"] = len(ORDERS) + 1
    ORDERS[order["id"]] = order
    return order


def get_order(order_id: int) -> Optional[dict]:
    """Return one order or None."""
    return ORDERS.get(order_id)
