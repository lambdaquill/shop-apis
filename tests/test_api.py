from app import create_app


def test_health():
    client = create_app().test_client()
    assert client.get("/health").json == {"status": "ok"}


def test_create_order():
    client = create_app().test_client()
    r = client.post("/orders/", json={"product_id": 2, "quantity": 2})
    assert r.status_code == 201
