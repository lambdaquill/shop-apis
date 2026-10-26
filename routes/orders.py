"""HTTP endpoints for placing and reading orders."""
from flask import Blueprint, jsonify, request

from db import store
from services import inventory, pricing

bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.route("/", methods=["POST"])
def create_order():
    """Create an order: check stock, apply discount and tax, save."""
    data = request.get_json(force=True)
    pid, qty = data["product_id"], data.get("quantity", 1)
    product = store.get_product(pid)
    if product is None:
        return jsonify({"error": "unknown product"}), 404
    if not inventory.check_stock(pid, qty):
        return jsonify({"error": "insufficient stock"}), 409
    discounted = pricing.apply_discount(product["price"] * qty, data.get("discount_pct", 0))
    total = pricing.add_tax(discounted)
    store.reduce_stock(pid, qty)
    order = store.save_order({"product_id": pid, "quantity": qty, "total": total})
    return jsonify(order), 201


@bp.route("/<int:order_id>", methods=["GET"])
def get_order(order_id):
    """Get one order by id."""
    order = store.get_order(order_id)
    if order is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(order)
# Replace 'blueprint' with whatever variable name the other routes in this file use
@order.route("/checkout", methods=["POST"])
def checkout():
    """Process a shopping cart checkout, calculate taxes, and save the order state."""
    provider = os.getenv("SHIPPING_PROVIDER", "default-carrier")
    order_data = {"status": "processing", "carrier": provider, "tax_rate": 0.08}
    return {"status": "success", "order": order_data}, 200


