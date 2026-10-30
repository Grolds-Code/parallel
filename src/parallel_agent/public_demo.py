"""Read-only ASGI boundary for the fixed offline browser lab (no server startup)."""
import asyncio
import json

from . import web_demo

PATHS = frozenset(("/", "/app.js", "/style.css", "/api/evidence"))
QUERIES = {b"": "delete", **{
    f"scenario={name}".encode(): name for name in web_demo.SCENARIOS
}}
SECURITY_HEADERS = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"content-security-policy", b"default-src 'none'; script-src 'self'; style-src 'self'; "
     b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"strict-transport-security", b"max-age=31536000"),
    (b"cross-origin-resource-policy", b"same-origin"),
)


def error(status, message):
    return status, "application/json", json.dumps({"error": message}).encode()


def response(scope):
    """Validate before dispatch; accept only literal paths and fixed query bytes."""
    path = scope.get("path", "")
    query = scope.get("query_string", b"")
    raw = scope.get("raw_path", path.encode("utf-8"))
    if len(raw) > 128 or len(query) > 64:
        return error(414, "Request target too long")
    if path not in PATHS or raw != path.encode("ascii", errors="replace"):
        return error(404, "Not found")
    if path != "/api/evidence":
        if query:
            return error(400, "Invalid query")
        return web_demo.route(path)
    if query not in QUERIES:
        return error(400, "Invalid scenario query")
    data = web_demo.build_evidence(QUERIES[query])
    # The engine intentionally retains diagnostic exceptions for local callers.
    # Never publish those details (or a partial verdict) on the public interface.
    evaluations = [data["evaluation"], *[
        item["observed_evaluation"] for item in data["repair"]["history"]
    ]]
    if (any(e["action_error"] is not None for e in evaluations)
            or any(item["goal_error"] is not None for item in data["repair"]["history"])
            or data["repair"]["disposition"] in (
                "repair_error", "goal_error", "action_check_error", "invalid_response")):
        raise RuntimeError("Evidence computation failed")
    return 200, "application/json", json.dumps(data, allow_nan=False).encode()


async def application(scope, receive, send):
    """Minimal ASGI 3 app: no framework docs, mounts, cookies, CORS or write API."""
    if scope["type"] == "lifespan":
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    if scope["type"] != "http":
        return
    method = scope.get("method", "")
    extra_headers = []
    try:
        if method not in ("GET", "HEAD"):
            status, kind, body = error(405, "Method not allowed")
            extra_headers = [(b"allow", b"GET, HEAD")]
        elif any(name.lower() == b"transfer-encoding" or
                 (name.lower() == b"content-length" and value != b"0")
                 for name, value in scope.get("headers", ())):
            status, kind, body = error(400, "Request bodies are not accepted")
        else:
            # Read at most one empty event; never buffer or drain uploaded data.
            event = await asyncio.wait_for(receive(), timeout=1)
            if event["type"] == "http.disconnect":
                return
            if (event["type"] != "http.request" or event.get("body")
                    or event.get("more_body", False)):
                status, kind, body = error(400, "Request bodies are not accepted")
            else:
                status, kind, body = response(scope)
    except TimeoutError:
        status, kind, body = error(408, "Request timed out")
    except Exception:
        status, kind, body = error(500, "Evidence unavailable; no verdict available.")
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", (kind + "; charset=utf-8").encode()),
        (b"content-length", str(len(body)).encode()),
        *SECURITY_HEADERS, *extra_headers,
    ]})
    await send({"type": "http.response.body", "body": b"" if method == "HEAD" else body})
