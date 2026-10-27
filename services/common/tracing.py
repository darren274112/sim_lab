"""
轻量分布式追踪（纯标准库，无第三方依赖）
=============================================
目的：让"链路追踪"落到真实 Trace 数据上——而不是静态拓扑图。

三件事：
  1. W3C Trace Context 传播：解析/注入 `traceparent` 头，把一次跨服务调用串成一条 trace；
  2. ASGI 中间件：为每个入站 HTTP 请求生成 SERVER span（状态码、耗时、错误标记）；
  3. 导出：以 OTLP/HTTP JSON 批量推送到 Jaeger（collector :4318），异步、有界队列、
     失败只计数不抛错，绝不影响业务请求。

环境变量：
  TRACING_ENABLED     默认 1（置 0 完全关闭，零开销）
  OTLP_ENDPOINT       默认 http://jaeger:4318/v1/traces
  TRACE_SAMPLE_RATE   默认 1.0（全采样）
  TRACE_MAX_QUEUE     默认 4096
"""
import contextvars
import json
import os
import platform
import queue
import secrets
import socket
import threading
import time
import urllib.parse
import urllib.request

SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
HOSTNAME = os.getenv("HOSTNAME", socket.gethostname())
OTLP_ENDPOINT = os.getenv("OTLP_ENDPOINT", "http://jaeger:4318/v1/traces")
TRACING_ENABLED = os.getenv("TRACING_ENABLED", "1") not in ("0", "false", "False")
SAMPLE_RATE = float(os.getenv("TRACE_SAMPLE_RATE", "1.0"))
MAX_QUEUE = int(os.getenv("TRACE_MAX_QUEUE", "4096"))

# span kind（OTLP 枚举）
KIND_INTERNAL, KIND_SERVER, KIND_CLIENT = 1, 2, 3
# status code（OTLP 枚举）
STATUS_UNSET, STATUS_OK, STATUS_ERROR = 0, 1, 2

_current = contextvars.ContextVar("current_span", default=None)


def _hex(nbytes):
    return secrets.token_hex(nbytes)


def new_trace_id():
    return _hex(16)


def new_span_id():
    return _hex(8)


# ----------------------------------------------------------------------
# W3C traceparent
# ----------------------------------------------------------------------
def parse_traceparent(header):
    """'00-<trace_id 32hex>-<span_id 16hex>-<flags>' -> (trace_id, span_id) 或 (None, None)"""
    if not header:
        return None, None
    parts = header.strip().split("-")
    if len(parts) < 4:
        return None, None
    trace_id, span_id = parts[1], parts[2]
    if len(trace_id) != 32 or len(span_id) != 16:
        return None, None
    try:
        int(trace_id, 16)
        int(span_id, 16)
    except ValueError:
        return None, None
    return trace_id, span_id


def format_traceparent(trace_id, span_id, sampled=True):
    return f"00-{trace_id}-{span_id}-{'01' if sampled else '00'}"


# ----------------------------------------------------------------------
# Span
# ----------------------------------------------------------------------
class Span:
    __slots__ = ("trace_id", "span_id", "parent_span_id", "name", "kind",
                 "start_ns", "end_ns", "attrs", "error")

    def __init__(self, name, kind=KIND_SERVER, trace_id=None, parent_span_id=None):
        self.trace_id = trace_id or new_trace_id()
        self.span_id = new_span_id()
        self.parent_span_id = parent_span_id
        self.name = name
        self.kind = kind
        self.start_ns = time.time_ns()
        self.end_ns = None
        self.attrs = {}
        self.error = False

    def set_tag(self, key, value):
        if value is not None:
            self.attrs[key] = value
        return self

    def fail(self, msg=None):
        self.error = True
        if msg:
            self.attrs["error.message"] = str(msg)[:300]
        return self

    def end(self):
        if self.end_ns is None:
            self.end_ns = time.time_ns()
        return self

    @property
    def duration_ms(self):
        end = self.end_ns or time.time_ns()
        return round((end - self.start_ns) / 1e6, 3)

    def to_otlp(self):
        attrs = [{"key": k, "value": _otlp_value(v)} for k, v in self.attrs.items()]
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id or "",
            "name": self.name,
            "kind": self.kind,
            "startTimeUnixNano": str(self.start_ns),
            "endTimeUnixNano": str(self.end_ns or time.time_ns()),
            "attributes": attrs,
            "status": {"code": STATUS_ERROR if self.error else STATUS_OK},
        }


def _otlp_value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


# ----------------------------------------------------------------------
# 导出器：有界队列 + 后台线程批量 POST（OTLP/HTTP JSON）
# ----------------------------------------------------------------------
class _Exporter:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.q = queue.Queue(maxsize=MAX_QUEUE)
        self.sent = 0
        self.failed = 0
        self.dropped = 0
        self.batches = 0
        self.last_error = None
        self.last_export_ts = None
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, name="otlp-exporter", daemon=True).start()
        return self

    def submit(self, span):
        try:
            self.q.put_nowait(span)
        except queue.Full:
            with self.lock:
                self.dropped += 1

    def _loop(self):
        while True:
            try:
                first = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < 256:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            self._send(batch)

    def _send(self, spans):
        by_service = {}
        for s in spans:
            by_service.setdefault(SERVICE_NAME, []).append(s.to_otlp())
        payload = {"resourceSpans": [
            {
                "resource": {"attributes": [
                    {"key": "service.name", "value": {"stringValue": svc}},
                    {"key": "service.instance.id", "value": {"stringValue": HOSTNAME}},
                    {"key": "telemetry.sdk.name", "value": {"stringValue": "sim-lab-tracing"}},
                    {"key": "telemetry.sdk.language", "value": {"stringValue": "python"}},
                    {"key": "telemetry.sdk.version", "value": {"stringValue": "1.0.0"}},
                ]},
                "scopeSpans": [{
                    "scope": {"name": "sim_lab.tracing", "version": "1.0"},
                    "spans": otlp_spans,
                }],
            }
            for svc, otlp_spans in by_service.items()
        ]}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            with self.lock:
                self.sent += len(spans)
                self.batches += 1
                self.last_export_ts = time.time()
                self.last_error = None
        except Exception as e:  # 网络/后端不可用：只计数，不打断业务
            with self.lock:
                self.failed += len(spans)
                self.last_error = f"{type(e).__name__}: {e}"[:200]

    def stats(self):
        with self.lock:
            return {
                "service": SERVICE_NAME,
                "endpoint": self.endpoint,
                "enabled": TRACING_ENABLED,
                "queue_len": self.q.qsize(),
                "spans_sent": self.sent,
                "spans_failed": self.failed,
                "spans_dropped": self.dropped,
                "batches": self.batches,
                "last_export_ts": self.last_export_ts,
                "last_error": self.last_error,
            }


_exporter = None


def exporter():
    global _exporter
    if _exporter is None:
        _exporter = _Exporter(OTLP_ENDPOINT)
        if TRACING_ENABLED:
            _exporter.start()
    return _exporter


def _finish(span):
    span.end()
    if TRACING_ENABLED:
        exporter().submit(span)
    return span


# ----------------------------------------------------------------------
# 上下文 API（供业务代码使用）
# ----------------------------------------------------------------------
def current_span():
    return _current.get()


def trace_headers():
    """生成下游调用要带上的 traceparent（父 span = 当前 span）。"""
    sp = current_span()
    if sp is None or not TRACING_ENABLED:
        return {}
    return {"traceparent": format_traceparent(sp.trace_id, sp.span_id)}


class client_span:
    """下游调用 span（CLIENT）。用法：

        with client_span("GET sensor-service/tracks", "http://sensor-service:8080/tracks") as sp:
            headers = trace_headers()   # 已指向该 client span
    """

    def __init__(self, name, url=None):
        parent = current_span()
        self.span = Span(name, kind=KIND_CLIENT,
                         trace_id=parent.trace_id if parent else None,
                         parent_span_id=parent.span_id if parent else None)
        if url:
            self.span.set_tag("http.url", url)
            # peer.service：被调用方服务名，故障定位时用于把"调用失败"归到正确的服务上
            host = urllib.parse.urlparse(url).hostname
            if host:
                self.span.set_tag("peer.service", host)
        self._token = None

    def __enter__(self):
        self._token = _current.set(self.span)
        return self.span

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.span.fail(exc)
        if self._token is not None:
            _current.reset(self._token)
        _finish(self.span)
        return False


def traced_json_request(method, url, body=None, timeout=3.0, span_name=None):
    """带链路传播的 HTTP 调用（返回 (status, parsed_json_or_text)）。

    用于服务间同步调用：自动开 CLIENT span、注入 traceparent、记录状态码与耗时。
    失败不抛异常，status 置 0 并标记 error，由调用方决定降级策略。
    """
    name = span_name or f"{method} {urllib.parse.urlparse(url).path}"
    with client_span(name, url) as sp:
        sp.set_tag("http.method", method)
        headers = {"Accept": "application/json"}
        headers.update(trace_headers())
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                sp.set_tag("http.status_code", resp.status)
                if resp.status >= 500:
                    sp.fail(f"HTTP {resp.status}")
                try:
                    return resp.status, json.loads(raw)
                except ValueError:
                    return resp.status, raw.decode(errors="replace")
        except Exception as e:
            sp.set_tag("http.status_code", 0)
            sp.fail(e)
            return 0, {"error": str(e)}


# ----------------------------------------------------------------------
# ASGI 中间件：入站请求 = SERVER span
# ----------------------------------------------------------------------
_SKIP_PATHS = ("/metrics", "/tracez")


class TracingMiddleware:
    def __init__(self, app, service=None):
        self.app = app
        self.service = service or SERVICE_NAME

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not TRACING_ENABLED:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        # /metrics 是 Prometheus 每 5s 的抓取（还有 /metrics/ 重定向），
        # 若给它建 span，会产生大量单 span 的"根 trace"把业务链路挤出采样窗口。
        if path in _SKIP_PATHS or path.startswith("/metrics"):
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        trace_id, parent_span = parse_traceparent(headers.get("traceparent"))
        if trace_id is None and SAMPLE_RATE < 1.0 and secrets.randbits(16) / 65536.0 > SAMPLE_RATE:
            return await self.app(scope, receive, send)

        span = Span(f"{scope.get('method', 'GET')} {path}",
                    kind=KIND_SERVER, trace_id=trace_id, parent_span_id=parent_span)
        span.set_tag("http.method", scope.get("method"))
        span.set_tag("http.route", path)
        span.set_tag("service.name", self.service)
        span.set_tag("upstream", "root" if parent_span is None else parent_span)
        token = _current.set(span)
        state = {"status": 500}

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                state["status"] = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            span.fail(e)
            raise
        finally:
            span.set_tag("http.status_code", state["status"])
            if state["status"] >= 500:
                span.fail(f"HTTP {state['status']}")
            _current.reset(token)
            _finish(span)

        return None


def stats():
    return exporter().stats() if TRACING_ENABLED else {"enabled": False, "service": SERVICE_NAME}
