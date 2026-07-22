"""Response security headers.

Applied to every response. Values are conservative but deliberately compatible
with the current single-file board UI, which inlines its own <style> and
<script>. That forces 'unsafe-inline' in the CSP today — the honest trade-off is
recorded here rather than hidden, and it goes away when the UI is rebuilt with
external assets or nonces.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings

# script-src is 'self' only: the built SPA ships external bundles and the
# theme-init script was moved out of index.html precisely so inline scripts can
# be forbidden outright.
# style-src still needs 'unsafe-inline' because React sets inline style
# attributes (style={{...}}), which style-src-attr covers via 'unsafe-inline'.
# connect-src 'self' covers the board's polling of /api/*.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

_BASE_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"strict-origin-when-cross-origin",
    b"permissions-policy": b"geolocation=(), microphone=(), camera=()",
    b"content-security-policy": _CSP.encode(),
}


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are buffered into memory.

    A single point of defence for every JSON endpoint: without it, an
    authenticated token could POST a multi-megabyte task spec or message and
    bloat the database (or exhaust memory). Counts bytes as they stream in rather
    than trusting Content-Length, which can be absent or wrong.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "DELETE"):
            await self.app(scope, receive, send)
            return

        # Fast path: an honest, oversized Content-Length is rejected immediately.
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit() and int(value) > self.max_bytes:
                await self._reject(send)
                return

        seen = 0

        async def _counting_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, _counting_receive, send)
        except _BodyTooLarge:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                    "body": b'{"detail":"Request body too large"}'})


class _BodyTooLarge(Exception):
    pass


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware — avoids BaseHTTPMiddleware, which buffers responses
    and would interfere with the MCP streamable-HTTP endpoint."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.headers = dict(_BASE_HEADERS)
        if settings.is_production:
            # Only meaningful over HTTPS; Cloud Run terminates TLS for us.
            self.headers[b"strict-transport-security"] = \
                b"max-age=31536000; includeSubDomains"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message.setdefault("headers", [])
                for key, value in self.headers.items():
                    if key not in existing:      # never clobber a handler's own header
                        message["headers"].append((key, value))
            await send(message)

        await self.app(scope, receive, _send)
