from common.base import create_app, REQUEST_COUNT
import os

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "sensor-service")

tracks = []

@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "tracks": len(tracks)}

@app.get("/tracks")
def get_tracks():
    REQUEST_COUNT.labels(method="GET", endpoint="/tracks").inc()
    return {"tracks": tracks}
