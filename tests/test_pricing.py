import pytest
from services.pricing import add_tax, apply_discount


def test_discount():
    assert apply_discount(100, 10) == 90.0


def test_discount_invalid():
    with pytest.raises(ValueError):
        apply_discount(100, 150)


def test_tax():
    assert add_tax(100) == 118.0
