from common.base import create_app, REQUEST_COUNT
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "env-service")

weather = {"temp": 15, "visibility": 8000, "wind": "5m/s", "condition": "clear"}

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "weather": weather}

@app.get("/weather")
def get_weather():
    REQUEST_COUNT.labels(method="GET", endpoint="/weather").inc()
    return weather
