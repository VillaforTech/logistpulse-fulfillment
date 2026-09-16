from decimal import Decimal


def stock_risk(stock, forecast):
    if stock < forecast * Decimal("0.75"):
        return "HIGH"
    return "MEDIUM" if stock < forecast else "LOW"
