# ROSS 是 C 语言离散事件仿真库，不是微服务，本身不需要单独对外提供 Service。
# 这里用多阶段构建把 ROSS + ross-sim（mil_sim）编译进 sim-bridge 镜像，
# 由 sim-bridge 通过子进程调用，因此运行时只需要 sim-bridge 这一个镜像。
FROM python:3.11-slim AS ross-builder

ARG ROSS_VERSION=8.1.1
# 当前网络访问 GitHub 直链不稳定，codeload 可用；可用 --build-arg 覆盖
ARG ROSS_URL=https://codeload.github.com/ross-org/ROSS/tar.gz/refs/tags/v${ROSS_VERSION}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake curl ca-certificates mpich libmpich-dev \
    && rm -rf /var/lib/apt/lists/*

# 编译 ROSS（安装到 /opt/ross）
# 说明：buildah 的 overlay 下 tar 恢复目录权限会报 Operation not permitted，
# 但文件已正确解出，因此该错误可忽略，随后用 test 确认源码完整。
RUN mkdir -p /src/ross \
    && curl -4 -fsSL --retry 3 --connect-timeout 15 "$ROSS_URL" -o /tmp/ross.tar.gz \
    && (tar -xzf /tmp/ross.tar.gz -C /src/ross --strip-components=1 || true) \
    && test -f /src/ross/CMakeLists.txt \
    && rm -f /tmp/ross.tar.gz \
    && cmake -S /src/ross -B /src/ross/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/ross \
    && cmake --build /src/ross/build -j"$(nproc)" \
    && cmake --install /src/ross/build

# 编译 ross-sim（mil_sim）
COPY ross-sim /src/ross-sim
RUN cmake -S /src/ross-sim -B /src/ross-sim/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DROSS_DIR=/opt/ross \
    && cmake --build /src/ross-sim/build -j"$(nproc)"


FROM python:3.11-slim

# mil_sim 动态链接 libmpich，运行阶段只需要 MPI 运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmpich12 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/common /app/common
COPY services/sim-bridge/requirements.txt .
# 默认使用国内 PyPI 镜像，可用 --build-arg PIP_INDEX_URL=https://pypi.org/simple 覆盖
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt

COPY services/sim-bridge/app.py .
COPY --from=ross-builder /src/ross-sim/build/mil_sim /app/ross-sim/build/mil_sim
RUN chmod +x /app/ross-sim/build/mil_sim

# 单进程仿真只需要共享内存/self 传输，避免 UCX 在容器内选错网卡
ENV SERVICE_NAME=sim-bridge \
    MIL_SIM_PATH=/app/ross-sim/build/mil_sim \
    UCX_TLS=sm,self

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
