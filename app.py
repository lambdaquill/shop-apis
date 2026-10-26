"""Application factory: builds the Flask app and registers blueprints."""
from flask import Flask, jsonify

import config
from routes import orders, products


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.register_blueprint(products.bp)
    app.register_blueprint(orders.bp)

    @app.route("/health", methods=["GET"])
    def health():
        """Liveness probe."""
        return jsonify({"status": "ok"})

    return app


if __name__ == "__main__":
    create_app().run(port=config.PORT, debug=config.DEBUG)
