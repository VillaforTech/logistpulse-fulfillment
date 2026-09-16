import time
from fastapi import Response
from prometheus_client import Counter, Histogram, CONTENT_TYPE_LATEST, generate_latest

REQUESTS = Counter(
    "logistpulse_http_requests_total",
    "HTTP requests",
    ["service", "method", "path", "status"],
)
LATENCY = Histogram(
    "logistpulse_http_request_duration_seconds", "HTTP latency", ["service", "path"]
)


def instrument(app, service):
    @app.middleware("http")
    async def measure(request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        REQUESTS.labels(service, request.method, path, str(response.status_code)).inc()
        LATENCY.labels(service, path).observe(time.monotonic() - started)
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
