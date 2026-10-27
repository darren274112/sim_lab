"""通用认证服务：签发 / 校验带 HMAC 签名的访问令牌。

平台类服务，供 api-gateway 与实验工作流复用。仅使用标准库，令牌无状态、
可被任意副本校验（密钥来自环境变量 AUTH_SECRET）。
"""
from common.base import create_app, REQUEST_COUNT
import base64
import hashlib
import hmac
import json
import os
import time

app = create_app()
SERVICE_NAME = os.getenv("SERVICE_NAME", "auth-service")
SECRET = os.getenv("AUTH_SECRET", "sim-lab-dev-secret").encode()
DEFAULT_TTL = int(os.getenv("AUTH_TTL_S", "3600"))

_issued = 0


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    return _b64e(hmac.new(SECRET, payload.encode(), hashlib.sha256).digest())


@app.get("/")
def root():
    REQUEST_COUNT.labels(method="GET", endpoint="/").inc()
    return {"service": SERVICE_NAME, "role": "auth", "issued": _issued,
            "ttl_s": DEFAULT_TTL}


@app.post("/auth/token")
def issue(body: dict):
    """签发令牌：body = {subject, role, ttl_s?}"""
    REQUEST_COUNT.labels(method="POST", endpoint="/auth/token").inc()
    global _issued
    subject = str(body.get("subject") or "anonymous")
    role = str(body.get("role") or "viewer")
    ttl = int(body.get("ttl_s") or DEFAULT_TTL)
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl,
        "jti": f"{subject}-{int(time.time() * 1000) % 100000}",
    }
    raw = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    _issued += 1
    return {"token": f"{raw}.{_sign(raw)}", "claims": payload, "token_type": "HMAC-SHA256"}


@app.post("/auth/verify")
def verify(body: dict):
    """校验令牌：返回 valid / claims / reason。"""
    REQUEST_COUNT.labels(method="POST", endpoint="/auth/verify").inc()
    token = str(body.get("token") or "")
    if "." not in token:
        return {"valid": False, "reason": "malformed"}
    raw, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(raw)):
        return {"valid": False, "reason": "bad_signature"}
    try:
        claims = json.loads(_b64d(raw))
    except (ValueError, json.JSONDecodeError):
        return {"valid": False, "reason": "bad_payload"}
    if int(claims.get("exp", 0)) < int(time.time()):
        return {"valid": False, "reason": "expired", "claims": claims}
    return {"valid": True, "claims": claims}


@app.get("/auth/policy")
def policy():
    REQUEST_COUNT.labels(method="GET", endpoint="/auth/policy").inc()
    return {
        "roles": {
            "admin": ["read", "write", "delete", "chaos"],
            "operator": ["read", "write", "chaos"],
            "viewer": ["read"],
        },
        "token_type": "HMAC-SHA256",
        "ttl_s": DEFAULT_TTL,
    }
