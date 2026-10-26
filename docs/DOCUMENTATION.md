# shop-apis — Documentation

> Maintained by **doc-agent**. `AUTO:*` blocks are regenerated from the code on every push; `NARRATIVE:*` and `AGENT:*` blocks are written by an AI agent. Add your own text anywhere outside these blocks and it will be preserved.

## Overview

<!-- NARRATIVE:overview -->
`shop-apis` is a lightweight sample e-commerce REST API built with Python and Flask. It provides endpoints for browsing a product catalog and placing orders with automated pricing calculations and inventory tracking. The repository serves both as a minimal API backend and as a sample codebase for testing `doc-agent`, an automated documentation maintainer.

Key capabilities include:
- **Product Management:** List available products or fetch details for a single item.
- **Order Processing:** Place orders with stock availability checks, percentage discount applications, and tax calculations.
- **In-Memory Store:** In-memory state storage for products and order history.
- **Health Checks:** A `/health` liveness probe endpoint.
<!-- /NARRATIVE:overview -->

## At a Glance

<!-- AUTO:glance -->
- **Files:** 15
- **Languages:** Python (13 files, 185 lines), Markdown (1 files, 77 lines)
- **Python modules:** 7 (14 top-level functions)
- **HTTP/RPC endpoints:** 5
- **Data models:** 0
- **Test files:** 2
- **Dependencies declared:** 1
- **Environment variables:** 4
- **Third-party Python libraries imported:** `flask`
<!-- /AUTO:glance -->

## Architecture

<!-- AUTO:architecture -->
```mermaid
flowchart LR
  client([Client])
  subgraph m_app["app.py"]
    m_app_e0["GET /health"]
  end
  m_config["config.py"]
  m_db_store["db/store.py"]
  subgraph m_routes_orders["routes/orders.py"]
    m_routes_orders_e0["POST /orders"]
    m_routes_orders_e1["GET /orders/{order_id}"]
  end
  subgraph m_routes_products["routes/products.py"]
    m_routes_products_e0["GET /products"]
    m_routes_products_e1["GET /products/{product_id}"]
  end
  m_services_inventory["services/inventory.py"]
  m_services_pricing["services/pricing.py"]
  client --> m_app_e0
  client --> m_routes_orders_e0
  client --> m_routes_orders_e1
  client --> m_routes_products_e0
  client --> m_routes_products_e1
  m_app --> m_config
  m_app --> m_routes_orders
  m_app --> m_routes_products
  m_routes_orders --> m_db_store
  m_routes_orders --> m_services_inventory
  m_routes_orders --> m_services_pricing
  m_routes_products --> m_db_store
  m_services_inventory --> m_config
  m_services_inventory --> m_db_store
  m_services_pricing --> m_config
  ext_flask(["flask"])
  m_app -.-> ext_flask
  m_routes_orders -.-> ext_flask
  m_routes_products -.-> ext_flask
```
<!-- /AUTO:architecture -->

<!-- NARRATIVE:architecture_notes -->
The application follows a standard layered architecture for Flask REST services:

- **Application Factory & Routing (`app.py`, `routes/`):** `app.py` exposes `create_app()` to construct the Flask instance and register application blueprints:
  - `routes/products.py` (`/products`): Endpoint handlers for retrieving product catalogue details.
  - `routes/orders.py` (`/orders`): Endpoint handlers for submitting and retrieving order details.
  - `/health`: Direct liveness endpoint registered on the application root.
- **Service Layer (`services/`):** Encapsulates core business rules separate from HTTP request/response logic:
  - `services/inventory.py`: Validates item availability against product stock and determines low-stock alerts based on `LOW_STOCK_THRESHOLD`.
  - `services/pricing.py`: Computes price reductions using percentage discounts and applies tax according to `TAX_RATE`.
- **Data Persistence (`db/store.py`):** Holds product items (`PRODUCTS`) and order records (`ORDERS`) in memory. Handles atomic stock decrements and auto-increment order IDs.
- **Configuration (`config.py`):** Loads operational settings from environment variables (`PORT`, `DEBUG`, `TAX_RATE`, `LOW_STOCK_THRESHOLD`) with sensible defaults.
<!-- /NARRATIVE:architecture_notes -->

## Request Flows

<!-- AUTO:flows -->
#### `POST /orders`

Handler: `create_order` in `routes/orders.py`

```mermaid
sequenceDiagram
  autonumber
  actor Client
  participant p_routes_orders as routes/orders.py
  participant p_db_store as db/store.py
  participant p_services_inventory as services/inventory.py
  participant p_services_pricing as services/pricing.py
  Client->>p_routes_orders: POST /orders
  p_routes_orders->>p_db_store: get_product()
  p_db_store-->>p_routes_orders: result
  p_routes_orders->>p_services_inventory: check_stock()
  p_services_inventory->>p_db_store: get_product()
  p_db_store-->>p_services_inventory: result
  p_services_inventory-->>p_routes_orders: result
  p_routes_orders->>p_services_pricing: apply_discount()
  p_services_pricing-->>p_routes_orders: result
  p_routes_orders->>p_services_pricing: add_tax()
  p_services_pricing-->>p_routes_orders: result
  p_routes_orders->>p_db_store: reduce_stock()
  p_db_store-->>p_routes_orders: result
  p_routes_orders->>p_db_store: save_order()
  p_db_store-->>p_routes_orders: result
  p_routes_orders-->>Client: response
```

#### `GET /orders/{order_id}`

Handler: `get_order` in `routes/orders.py`

```mermaid
sequenceDiagram
  autonumber
  actor Client
  participant p_routes_orders as routes/orders.py
  participant p_db_store as db/store.py
  Client->>p_routes_orders: GET /orders/{order_id}
  p_routes_orders->>p_db_store: get_order()
  p_db_store-->>p_routes_orders: result
  p_routes_orders-->>Client: response
```

#### `GET /products`

Handler: `list_products` in `routes/products.py`

```mermaid
sequenceDiagram
  autonumber
  actor Client
  participant p_routes_products as routes/products.py
  participant p_db_store as db/store.py
  Client->>p_routes_products: GET /products
  p_routes_products->>p_db_store: list_products()
  p_db_store-->>p_routes_products: result
  p_routes_products-->>Client: response
```

#### `GET /products/{product_id}`

Handler: `get_product` in `routes/products.py`

```mermaid
sequenceDiagram
  autonumber
  actor Client
  participant p_routes_products as routes/products.py
  participant p_db_store as db/store.py
  Client->>p_routes_products: GET /products/{product_id}
  p_routes_products->>p_db_store: get_product()
  p_db_store-->>p_routes_products: result
  p_routes_products-->>Client: response
```
<!-- /AUTO:flows -->

## Project Structure

<!-- AUTO:structure -->
```text
shop-apis/
├── db/
│   ├── __init__.py
│   └── store.py  # In-memory data store for products and orders (swap for a real DB later).
├── routes/
│   ├── __init__.py
│   ├── orders.py  # HTTP endpoints for placing and reading orders.
│   └── products.py  # HTTP endpoints for the product catalogue.
├── services/
│   ├── __init__.py
│   ├── inventory.py  # Stock checks on top of the data store.
│   └── pricing.py  # Pricing rules: discounts and tax.
├── tests/
│   ├── __init__.py
│   ├── test_api.py
│   └── test_pricing.py
├── README.md  # Project readme
├── app.py  # Application factory: builds the Flask app and registers blueprints.
├── config.py  # Central configuration, read from environment variables.
└── requirements.txt  # Python dependencies
```
<!-- /AUTO:structure -->

## API Reference

<!-- AUTO:api -->
| Method | Path | Handler | Location | Description |
|---|---|---|---|---|
| `GET` | `/health` | `health` | `app.py:15` | Liveness probe. |
| `POST` | `/orders` | `create_order` | `routes/orders.py:11` | Create an order: check stock, apply discount and tax, save. |
| `GET` | `/orders/{order_id}` | `get_order` | `routes/orders.py:28` | Get one order by id. |
| `GET` | `/products` | `list_products` | `routes/products.py:10` | List all products. |
| `GET` | `/products/{product_id}` | `get_product` | `routes/products.py:16` | Get one product by id. |
<!-- /AUTO:api -->

## Modules

<!-- AUTO:modules -->
### `app.py`

Application factory: builds the Flask app and registers blueprints.

| Signature | Kind | Description |
|---|---|---|
| `create_app() -> Flask` | function | Create and configure the Flask application. |

### `config.py`

Central configuration, read from environment variables.


### `db/store.py`

In-memory data store for products and orders (swap for a real DB later).

| Signature | Kind | Description |
|---|---|---|
| `list_products() -> list` | function | Return all products. |
| `get_product(product_id: int) -> Optional[dict]` | function | Return one product or None. |
| `reduce_stock(product_id: int, qty: int) -> None` | function | Decrease stock after a successful order. |
| `save_order(order: dict) -> dict` | function | Persist an order and assign it an id. |
| `get_order(order_id: int) -> Optional[dict]` | function | Return one order or None. |

### `routes/orders.py`

HTTP endpoints for placing and reading orders.

| Signature | Kind | Description |
|---|---|---|
| `create_order()` | function | Create an order: check stock, apply discount and tax, save. |
| `get_order(order_id)` | function | Get one order by id. |

### `routes/products.py`

HTTP endpoints for the product catalogue.

| Signature | Kind | Description |
|---|---|---|
| `list_products()` | function | List all products. |
| `get_product(product_id)` | function | Get one product by id. |

### `services/inventory.py`

Stock checks on top of the data store.

| Signature | Kind | Description |
|---|---|---|
| `check_stock(product_id: int, qty: int) -> bool` | function | True if enough stock is available. |
| `is_low_stock(product_id: int) -> bool` | function | True if stock is at or below LOW_STOCK_THRESHOLD. |

### `services/pricing.py`

Pricing rules: discounts and tax.

| Signature | Kind | Description |
|---|---|---|
| `apply_discount(price: float, pct: float) -> float` | function | Apply a percentage discount (0-100) to a price. |
| `add_tax(amount: float) -> float` | function | Add TAX_RATE tax to an amount. |
<!-- /AUTO:modules -->

## Dependencies

<!-- AUTO:dependencies -->
| Package | Version | Source |
|---|---|---|
| `flask` | `>=3.0` | requirements.txt |
<!-- /AUTO:dependencies -->

## Configuration

<!-- AUTO:env -->
| Variable | Default | Used in |
|---|---|---|
| `DEBUG` | `'false'` | `config.py` |
| `LOW_STOCK_THRESHOLD` | `'5'` | `config.py` |
| `PORT` | `'5000'` | `config.py` |
| `TAX_RATE` | `'0.18'` | `config.py` |
<!-- /AUTO:env -->

## Setup & Usage

<!-- NARRATIVE:setup_usage -->
### Prerequisites

- Python 3.9 or higher
- `pip` package manager

### Local Installation

1. Clone the repository and enter the project directory:
   ```bash
   git clone <repository-url>
   cd shop-apis
   ```

2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```

### Running the Application

Start the server using Python:
```bash
python app.py
```
By default, the API runs at `http://127.0.0.1:5000`.

### Configuration Environment Variables

Configure application parameters using environment variables:

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | Port on which the HTTP server listens. |
| `DEBUG` | `false` | Enable Flask debug mode (`true` or `false`). |
| `TAX_RATE` | `0.18` | Tax rate applied to orders (e.g., `0.18` = 18%). |
| `LOW_STOCK_THRESHOLD` | `5` | Threshold quantity for low-stock determinations. |

### Running Tests

Execute the unit test suite with `pytest`:
```bash
pytest
```

### Example Requests

**Get all products:**
```bash
curl http://127.0.0.1:5000/products
```

**Get product details:**
```bash
curl http://127.0.0.1:5000/products/1
```

**Submit an order:**
```bash
curl -X POST http://127.0.0.1:5000/orders \
  -H "Content-Type: application/json" \
  -d '{"product_id": 2, "quantity": 3, "discount_pct": 10}'
```

**Retrieve an order:**
```bash
curl http://127.0.0.1:5000/orders/1
```
<!-- /NARRATIVE:setup_usage -->

## Tests

<!-- AUTO:tests -->
| File | # tests | Test functions |
|---|---|---|
| `tests/test_api.py` | 2 | `test_health`, `test_create_order` |
| `tests/test_pricing.py` | 3 | `test_discount`, `test_discount_invalid`, `test_tax` |
<!-- /AUTO:tests -->
