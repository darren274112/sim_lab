from fastapi import FastAPI
from prometheus_client import make_asgi_app, Counter, Gauge
import os
import time

SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
START_TIME = time.time()

REQUEST_COUNT = Counter(f'{SERVICE_NAME}_requests_total', 'Total requests', ['method', 'endpoint'])
HEALTH_STATUS = Gauge(f'{SERVICE_NAME}_health', 'Health status')
BUSINESS_READY = Gauge(f'{SERVICE_NAME}_business_ready', 'Business ready status')
UP_TIME = Gauge(f'{SERVICE_NAME}_uptime_seconds', 'Uptime')

def create_app():
    app = FastAPI(title=f"Military Sim Service - {SERVICE_NAME}")
    
    # Prometheus metrics
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)
    
    @app.get("/healthz")
    def healthz():
        REQUEST_COUNT.labels(method="GET", endpoint="/healthz").inc()
        HEALTH_STATUS.set(1)
        return {"status": "ok", "service": SERVICE_NAME, "uptime": time.time() - START_TIME}
    
    @app.get("/readyz/business")
    def readyz_business():
        REQUEST_COUNT.labels(method="GET", endpoint="/readyz/business").inc()
        BUSINESS_READY.set(1)
        return {"status": "ready", "service": SERVICE_NAME, "business": "ok", "deps": []}
    
    return app
