FROM python:3.12-slim AS runtime
WORKDIR /app
COPY services/runtime/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/logist /app/logist
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "logist.fulfillment:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim AS test
WORKDIR /app
COPY requirements-dev.txt /app/requirements-dev.txt
COPY services/runtime/requirements.txt /app/services/runtime/requirements.txt
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY services/logist /app/logist
COPY services/inventory-api/stock_risk.py /app/services/inventory-api/stock_risk.py
COPY tests /app/tests
ENV PYTHONPATH=/app
CMD ["pytest", "tests/unit", "tests/database", "-q", "--junitxml=/artifacts/database-tests.xml"]
