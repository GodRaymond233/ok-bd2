"""One local launch capability authenticates HTTP and WebSocket traffic.

Ported from ``codex/webui-refactor`` (``3211266``/``b8f623d``) and extended with a
``Bearer`` header for the desktop shell's main process, which talks to the core
without a browser cookie jar.
"""

import secrets
from http.cookies import CookieError, SimpleCookie

from starlette.responses import JSONResponse, RedirectResponse


class LocalSession:
    def __init__(self, app, *, token: str, origin: str):
        self.app = app
        self.token = token
        self.origin = origin
        self.authority = origin.removeprefix("http://")
        self.cookie_name = f"bd2_{token[:12]}"
        self.policy = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; "
            f"connect-src 'self' {origin.replace('http:', 'ws:')}; "
            "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        ).encode("ascii")

    def _bearer_matches(self, headers) -> bool:
        authorization = headers.get("authorization", "")
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not credential:
            return False
        return secrets.compare_digest(credential.strip(), self.token)

    def _cookie_matches(self, headers) -> bool:
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
            cookie = cookies.get(self.cookie_name)
            return cookie is not None and secrets.compare_digest(cookie.value, self.token)
        except (CookieError, ValueError, TypeError):
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = {key.decode("latin1"): value.decode("latin1") for key, value in scope["headers"]}
        origin = headers.get("origin")
        trusted_origin = (
            headers.get("host") == self.authority
            and (origin is None or origin == self.origin)
            and headers.get("sec-fetch-site") != "cross-site"
        )
        path = scope.get("path", "")
        if (
            trusted_origin
            and scope["type"] == "http"
            and scope["method"] == "GET"
            and path.startswith("/launch/")
            and secrets.compare_digest(path.removeprefix("/launch/"), self.token)
        ):
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(self.cookie_name, self.token, httponly=True, samesite="strict")
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return await response(scope, receive, send)
        authenticated = self._cookie_matches(headers) or self._bearer_matches(headers)
        if not trusted_origin or not authenticated:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            response = JSONResponse({"detail": "请从 ok-bd2 客户端重新打开界面"}, status_code=403)
            return await response(scope, receive, send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", self.policy),
                ]
            await send(message)

        return await self.app(scope, receive, secured_send)
