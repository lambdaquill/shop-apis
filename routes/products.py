"""HTTP endpoints for the product catalogue."""
from flask import Blueprint, jsonify

from db import store

bp = Blueprint("products", __name__, url_prefix="/products")


@bp.route("/", methods=["GET"])
def list_products():
    """List all products."""
    return jsonify(store.list_products())


@bp.route("/<int:product_id>", methods=["GET"])
def get_product(product_id):
    """Get one product by id."""
    product = store.get_product(product_id)
    if product is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(product)
