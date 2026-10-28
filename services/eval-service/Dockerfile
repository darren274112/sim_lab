FROM python:3.11-slim
WORKDIR /app
COPY services/common /app/common
COPY services/eval-service/requirements.txt .
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt
COPY services/eval-service/app.py .
ENV SERVICE_NAME=eval-service
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
