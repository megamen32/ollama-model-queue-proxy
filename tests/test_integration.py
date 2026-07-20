import http.client
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ollama_model_queue_proxy as queue_proxy


class FakeOllamaHandler(BaseHTTPRequestHandler):
    models: list[str] = []
    lock = threading.Lock()
    first_started = threading.Event()
    release_first = threading.Event()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        model = payload["model"]
        with self.lock:
            self.models.append(model)
            call_number = len(self.models)
        if call_number == 1:
            self.first_started.set()
            self.release_first.wait(timeout=2.0)
        body = json.dumps({"model": model, "response": "ok"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: object) -> None:
        return


class ProxyIntegrationTests(unittest.TestCase):
    def test_http_requests_are_grouped_by_active_model(self) -> None:
        FakeOllamaHandler.models = []
        FakeOllamaHandler.first_started.clear()
        FakeOllamaHandler.release_first.clear()
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()

        original_upstream = queue_proxy.UPSTREAM_URL
        original_grace = queue_proxy.BATCH_GRACE_S
        queue_proxy.UPSTREAM_URL = f"http://127.0.0.1:{upstream.server_address[1]}"
        queue_proxy.BATCH_GRACE_S = 0.05
        proxy = queue_proxy.ReusableThreadingHTTPServer(
            ("127.0.0.1", 0), queue_proxy.QueueHandler
        )
        proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        proxy_thread.start()

        results: list[int] = []
        results_lock = threading.Lock()

        def request(model: str) -> None:
            connection = http.client.HTTPConnection(*proxy.server_address, timeout=3)
            body = json.dumps({"model": model, "prompt": "integration"}).encode("utf-8")
            connection.request(
                "POST",
                "/api/generate",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read()
            with results_lock:
                results.append(response.status)
            connection.close()

        first_thread = threading.Thread(target=request, args=("qwen",))
        first_thread.start()
        self.assertTrue(FakeOllamaHandler.first_started.wait(timeout=1.0))
        second_thread = threading.Thread(target=request, args=("gemma",))
        same_model_thread = threading.Thread(target=request, args=("qwen",))
        second_thread.start()
        same_model_thread.start()
        time.sleep(0.05)
        FakeOllamaHandler.release_first.set()
        first_thread.join(timeout=3.0)
        second_thread.join(timeout=3.0)
        same_model_thread.join(timeout=3.0)

        proxy.shutdown()
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()
        queue_proxy.UPSTREAM_URL = original_upstream
        queue_proxy.BATCH_GRACE_S = original_grace

        self.assertEqual(results, [200, 200, 200])
        self.assertEqual(FakeOllamaHandler.models, ["qwen", "qwen", "gemma"])


if __name__ == "__main__":
    unittest.main()
