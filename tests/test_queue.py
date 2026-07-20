import threading
import time
import unittest

import ollama_model_queue_proxy as queue_proxy


class FakeHandler:
    client_address = ("127.0.0.1", 12345)


class ModelQueueTests(unittest.TestCase):
    def test_same_model_is_drained_before_switch(self) -> None:
        started_models: list[str] = []
        first_started = threading.Event()
        release_first = threading.Event()

        def executor(task: queue_proxy.QueueTask) -> None:
            started_models.append(task.model)
            if len(started_models) == 1:
                first_started.set()
                release_first.wait(timeout=2.0)

        model_queue = queue_proxy.ModelQueue(executor=executor)
        first = model_queue.enqueue(FakeHandler(), b"{}", "qwen")
        self.assertIsNotNone(first)
        self.assertTrue(first_started.wait(timeout=1.0))
        second = model_queue.enqueue(FakeHandler(), b"{}", "gemma")
        same_model = model_queue.enqueue(FakeHandler(), b"{}", "qwen")
        self.assertIsNotNone(second)
        self.assertIsNotNone(same_model)
        release_first.set()

        for task in (first, second, same_model):
            self.assertIsNotNone(task)
            self.assertTrue(task.done.wait(timeout=2.0))

        self.assertEqual(started_models, ["qwen", "qwen", "gemma"])

    def test_full_queue_rejects_new_work(self) -> None:
        original_max_queue = queue_proxy.MAX_QUEUE
        queue_proxy.MAX_QUEUE = 1
        try:
            release = threading.Event()

            def executor(task: queue_proxy.QueueTask) -> None:
                release.wait(timeout=2.0)

            model_queue = queue_proxy.ModelQueue(executor=executor)
            first = model_queue.enqueue(FakeHandler(), b"{}", "qwen")
            self.assertIsNotNone(first)
            deadline = time.monotonic() + 1.0
            while model_queue.active_model is None and time.monotonic() < deadline:
                time.sleep(0.01)
            queued = model_queue.enqueue(FakeHandler(), b"{}", "qwen")
            rejected = model_queue.enqueue(FakeHandler(), b"{}", "qwen")
            self.assertIsNotNone(queued)
            self.assertIsNone(rejected)
            release.set()
        finally:
            queue_proxy.MAX_QUEUE = original_max_queue


if __name__ == "__main__":
    unittest.main()
