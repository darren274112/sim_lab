FROM python:3.11-slim
WORKDIR /app
COPY services/common /app/common
COPY services/entity-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/entity-service/app.py .
ENV SERVICE_NAME=entity-service
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
