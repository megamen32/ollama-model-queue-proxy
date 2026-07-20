#!/usr/bin/env python3
"""Model-affine FIFO queue for Ollama-compatible HTTP endpoints.

Inference requests are serialized through one worker. While a model is active,
queued requests for that model are drained before switching to another model.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit


LOGGER = logging.getLogger("ollama-model-queue")
LISTEN_HOST = os.getenv("OLLAMA_QUEUE_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("OLLAMA_QUEUE_LISTEN_PORT", "11434"))
UPSTREAM_URL = os.getenv("OLLAMA_UPSTREAM_URL", "http://127.0.0.1:11435")
MAX_QUEUE = int(os.getenv("OLLAMA_MODEL_QUEUE_MAX", "128"))
BATCH_GRACE_S = float(os.getenv("OLLAMA_QUEUE_BATCH_GRACE_S", "0.25"))
FIRST_BYTE_TIMEOUT_S = float(os.getenv("OLLAMA_PROXY_FIRST_BYTE_TIMEOUT_S", "180"))
MAX_REQUEST_BYTES = int(os.getenv("OLLAMA_QUEUE_MAX_REQUEST_BYTES", str(64 * 1024 * 1024)))

INFERENCE_PATHS = {
    "/api/chat",
    "/api/generate",
    "/api/embeddings",
    "/v1/chat/completions",
    "/v1/embeddings",
}
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass
class QueueTask:
    handler: Any
    body: bytes
    model: str
    enqueued_at: float
    done: threading.Event


def extract_model(body: bytes) -> str:
    """Extract a stable model label from Ollama or OpenAI-compatible JSON."""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "__unknown__"
    if not isinstance(payload, dict):
        return "__unknown__"
    model = payload.get("model", "__unknown__")
    if isinstance(model, dict):
        model = model.get("name") or model.get("id") or "__unknown__"
    return str(model or "__unknown__")


class ModelQueue:
    """Single-worker queue that prefers the currently loaded model."""

    def __init__(self, executor: Callable[[QueueTask], None] | None = None) -> None:
        if MAX_QUEUE < 1:
            raise ValueError("OLLAMA_MODEL_QUEUE_MAX must be positive")
        self.condition = threading.Condition()
        self.tasks: deque[QueueTask] = deque()
        self.active_model: str | None = None
        self.executor = executor or self._execute_http_task
        self.worker = threading.Thread(
            target=self._run,
            name="ollama-model-queue",
            daemon=True,
        )
        self.worker.start()

    def enqueue(self, handler: Any, body: bytes, model: str) -> QueueTask | None:
        """Add a task without waiting; return None when the queue is full."""

        with self.condition:
            if len(self.tasks) >= MAX_QUEUE:
                return None
            task = QueueTask(handler, body, model, time.monotonic(), threading.Event())
            self.tasks.append(task)
            client = getattr(handler, "client_address", ("unknown",))[0]
            LOGGER.info(f"queue_enqueue model={model!r} depth={len(self.tasks)} client={client}")
            self.condition.notify_all()
            return task

    def submit(self, handler: Any, body: bytes, model: str) -> bool:
        """Queue a request and wait until its upstream response is complete."""

        task = self.enqueue(handler, body, model)
        if task is None:
            return False
        task.done.wait()
        return True

    def _take_next(self) -> QueueTask:
        with self.condition:
            while not self.tasks:
                self.condition.wait()

            if self.active_model is None:
                task = self.tasks.popleft()
                self.active_model = task.model
                return task

            same_model = next(
                (task for task in self.tasks if task.model == self.active_model),
                None,
            )
            if same_model is not None:
                self.tasks.remove(same_model)
                return same_model

            deadline = time.monotonic() + BATCH_GRACE_S
            while time.monotonic() < deadline:
                self.condition.wait(timeout=max(deadline - time.monotonic(), 0.0))
                same_model = next(
                    (task for task in self.tasks if task.model == self.active_model),
                    None,
                )
                if same_model is not None:
                    self.tasks.remove(same_model)
                    return same_model

            task = self.tasks.popleft()
            previous_model = self.active_model
            self.active_model = task.model
            LOGGER.info(
                f"queue_switch from={previous_model!r} to={self.active_model!r} "
                f"depth={len(self.tasks)}"
            )
            return task

    def _run(self) -> None:
        while True:
            task = self._take_next()
            wait_s = time.monotonic() - task.enqueued_at
            LOGGER.info(f"queue_start model={task.model!r} wait_s={wait_s:.3f}")
            try:
                self.executor(task)
            except Exception:
                LOGGER.exception(f"queue_worker_error model={task.model!r}")
                try:
                    task.handler.send_json(502, {"error": "queue worker error"})
                except Exception:
                    LOGGER.debug("could not send queue worker error", exc_info=True)
            finally:
                task.done.set()
                LOGGER.info(f"queue_done model={task.model!r}")

    @staticmethod
    def _execute_http_task(task: QueueTask) -> None:
        task.handler.forward_upstream(task.body)


QUEUE = ModelQueue()


class QueueHandler(BaseHTTPRequestHandler):
    """HTTP adapter that queues inference and forwards management requests."""

    protocol_version = "HTTP/1.0"
    server_version = "ollama-model-queue/1.0"

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "upstream": UPSTREAM_URL,
                    "active_model": QUEUE.active_model,
                    "queued_requests": len(QUEUE.tasks),
                    "max_queue": MAX_QUEUE,
                    "batch_grace_s": BATCH_GRACE_S,
                },
            )
            return
        self.forward_upstream(b"")

    def do_HEAD(self) -> None:
        self.forward_upstream(b"")

    def do_POST(self) -> None:
        self.handle_body_request()

    def do_PUT(self) -> None:
        self.handle_body_request()

    def handle_body_request(self) -> None:
        body = self.read_body()
        if body is None:
            return
        path = urlsplit(self.path).path
        if path not in INFERENCE_PATHS:
            self.forward_upstream(body)
            return
        model = extract_model(body)
        if not QUEUE.submit(self, body, model):
            self.send_json(429, {"error": "ollama model queue is full"})

    def read_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            self.send_json(400, {"error": f"invalid Content-Length: {exc}"})
            return None
        if content_length < 0 or content_length > MAX_REQUEST_BYTES:
            self.send_json(413, {"error": "request body is too large"})
            return None
        return self.rfile.read(content_length) if content_length else b""

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def forward_upstream(self, body: bytes) -> None:
        target = urlsplit(UPSTREAM_URL)
        if target.scheme not in {"http", "https"} or not target.hostname:
            self.send_json(502, {"error": "OLLAMA_UPSTREAM_URL must be http or https"})
            return
        target_path = f"{target.path.rstrip('/')}{urlsplit(self.path).path}"
        query = urlsplit(self.path).query
        if query:
            target_path = f"{target_path}?{query}"
        port = target.port or (443 if target.scheme == "https" else 80)
        connection_type = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(target.hostname, port, timeout=FIRST_BYTE_TIMEOUT_S)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS | {"host", "content-length"}
        }
        headers["Connection"] = "close"
        if body:
            headers["Content-Length"] = str(len(body))
        try:
            connection.request(self.command, target_path, body=body or None, headers=headers)
            response = connection.getresponse()
            if connection.sock is not None:
                connection.sock.settimeout(None)
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() in HOP_BY_HOP_HEADERS | {"content-length"}:
                    continue
                self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (http.client.HTTPException, OSError) as exc:
            LOGGER.warning(f"upstream_error path={self.path} error={exc}")
            if not self.wfile.closed:
                self.send_json(502, {"error": "upstream request failed"})
        finally:
            connection.close()

    def log_message(self, format_string: str, *args: Any) -> None:
        message = format_string % args
        LOGGER.info(f"{self.address_string()} {message}")


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> None:
    logging.basicConfig(
        level=os.getenv("OLLAMA_QUEUE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    LOGGER.info(
        f"listening host={LISTEN_HOST} port={LISTEN_PORT} upstream={UPSTREAM_URL} "
        f"max_queue={MAX_QUEUE} batch_grace_s={BATCH_GRACE_S:.3f}"
    )
    server = ReusableThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), QueueHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("shutdown requested")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
