FROM python:3.11-slim
WORKDIR /app
COPY services/common /app/common
COPY services/api-gateway/requirements.txt .
# 默认使用国内 PyPI 镜像，可用 --build-arg PIP_INDEX_URL=https://pypi.org/simple 覆盖
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt
COPY services/api-gateway/app.py .
ENV SERVICE_NAME=api-gateway
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
