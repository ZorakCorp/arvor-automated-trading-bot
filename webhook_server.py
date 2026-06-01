"""HTTP server for TradingView alert webhooks."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Callable
from urllib.parse import parse_qs, urlparse

from tv_signal_parser import parse_tradingview_payload

if TYPE_CHECKING:
    from trade_executor import TradeExecutor

logger = logging.getLogger(__name__)

SignalHandler = Callable[[object], dict[str, str]]


class _WebhookServer(ThreadingHTTPServer):
    """HTTPServer with shared handler config."""

    daemon_threads = True
    allow_reuse_address = True


def _read_body(handler: BaseHTTPRequestHandler, max_bytes: int = 16_384) -> str:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length > max_bytes:
        raise ValueError(f"Body too large ({length} bytes)")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace").strip()


def _secret_ok(path: str, headers: dict[str, str], expected_secret: str) -> bool:
    parsed = urlparse(path)
    query = parse_qs(parsed.query)
    token = (
        (query.get("secret") or query.get("token") or [""])[0]
        or headers.get("X-Webhook-Secret", "")
        or headers.get("X-Arvor-Secret", "")
    )
    return bool(token) and token == expected_secret


def make_webhook_handler(
    secret: str,
    on_signal: SignalHandler,
) -> type[BaseHTTPRequestHandler]:
    """Build request handler bound to secret + callback."""

    class WebhookHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: ARG002
            logger.debug("webhook %s", fmt % args)

        def _send(self, code: int, payload: dict[str, str]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/health"):
                self._send(200, {"status": "ok"})
                return
            self._send(404, {"status": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path not in ("/webhook", "/"):
                self._send(404, {"status": "not_found"})
                return

            headers = {k: v for k, v in self.headers.items()}
            if not _secret_ok(self.path, headers, secret):
                logger.warning("Webhook rejected: invalid or missing secret")
                self._send(401, {"status": "unauthorized"})
                return

            try:
                body = _read_body(self)
            except ValueError as exc:
                self._send(413, {"status": "error", "reason": str(exc)})
                return

            signal = parse_tradingview_payload(body)
            if signal is None:
                self._send(400, {"status": "error", "reason": "invalid_payload"})
                return

            logger.info(
                "TradingView webhook received: %s (source=%s)",
                signal.action,
                body[:120],
            )
            result = on_signal(signal)
            code = 200 if result.get("status") == "accepted" else 409
            self._send(code, result)

    return WebhookHandler


def start_webhook_server(
    port: int,
    secret: str,
    on_signal: SignalHandler,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """Start daemon webhook server thread. Returns (server, thread)."""
    handler = make_webhook_handler(secret, on_signal)
    server = _WebhookServer(("0.0.0.0", port), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="tv-webhook",
        daemon=True,
    )
    thread.start()
    logger.info("TradingView webhook listening on 0.0.0.0:%s (/webhook)", port)
    return server, thread
