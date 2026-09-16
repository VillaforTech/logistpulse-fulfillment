from decimal import Decimal
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location(
    "stock_risk",
    Path(__file__).resolve().parents[2] / "services/inventory-api/stock_risk.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "stock,forecast,risk",
    [
        ("38", "61", "HIGH"),
        ("45.75", "61", "MEDIUM"),
        ("61", "61", "LOW"),
        ("74", "52", "LOW"),
    ],
)
def test_numeric_postgres_values_keep_exact_risk_boundaries(stock, forecast, risk):
    assert module.stock_risk(Decimal(stock), Decimal(forecast)) == risk
