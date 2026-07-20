<p align="center">
  <img src="docs/ollama-queue-banner.svg" alt="Ollama model-affine queue" width="100%">
</p>

<h1 align="center">Ollama Model-Affine Queue</h1>

<p align="center">
  <a href="#english">English</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.zh-CN.md">中文</a>
</p>

> **EN** — A tiny, dependency-free HTTP proxy that serializes Ollama inference and drains queued work for the currently active model before switching to the next one.
>
> **RU** — Небольшой HTTP-прокси без зависимостей: он последовательно обслуживает inference-запросы Ollama, сначала дочищает очередь текущей загруженной модели и только потом переключается на следующую.
>
> **中文** — 一个零依赖的小型 HTTP 代理：串行处理 Ollama 推理请求，先完成当前已加载模型的排队任务，再切换到下一个模型。

## English

### Why

Ollama can serialize requests with `OLLAMA_NUM_PARALLEL=1`, but that does not express a useful scheduling policy when several models compete for one GPU. This proxy adds model affinity:

1. the first inference request selects the active model;
2. pending requests for that model are drained first;
3. the proxy waits briefly for more work for the same model;
4. only then does it switch to the oldest request for another model.

Requests wait in the queue. They are not cancelled just because another model is currently loaded. A bounded queue returns `429` only when its configured capacity is exhausted.

### Features

- standard-library-only Python implementation;
- native Ollama and OpenAI-compatible inference paths;
- one upstream inference worker, so one model is loaded at a time when Ollama is configured accordingly;
- model-affine batching with a small configurable grace period;
- `/health` endpoint with active model and queue depth;
- structured queue logs: `queue_enqueue`, `queue_start`, `queue_switch`, `queue_done`;
- no changes required in Ollama itself.

### Install

The proxy listens on `127.0.0.1:11437` and forwards to Ollama on `127.0.0.1:11434` by default.

```bash
sudo install -D -m 0755 ollama_model_queue_proxy.py \
  /usr/local/libexec/ollama_model_queue_proxy.py
sudo install -D -m 0644 systemd/ollama-model-queue-proxy.service \
  /etc/systemd/system/ollama-model-queue-proxy.service
sudo systemctl daemon-reload
sudo systemctl enable --now ollama-model-queue-proxy.service
```

For a single loaded model, keep Ollama aligned with the proxy:

```ini
# /etc/systemd/system/ollama.service.d/90-queue-runtime.conf
[Service]
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_MAX_QUEUE=64"
```

Then run `sudo systemctl daemon-reload && sudo systemctl restart ollama`.

Point LiteLLM or another OpenAI-compatible client at the proxy path instead of the direct Ollama port. For LiteLLM's Ollama provider, use `http://127.0.0.1:11437` as the `api_base`.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_QUEUE_LISTEN_HOST` | `127.0.0.1` | Proxy bind address |
| `OLLAMA_QUEUE_LISTEN_PORT` | `11437` | Proxy port |
| `OLLAMA_UPSTREAM_URL` | `http://127.0.0.1:11434` | Ollama URL |
| `OLLAMA_MODEL_QUEUE_MAX` | `128` | Maximum pending requests |
| `OLLAMA_QUEUE_BATCH_GRACE_S` | `0.25` | Wait for same-model arrivals before switching |
| `OLLAMA_PROXY_FIRST_BYTE_TIMEOUT_S` | `180` | Upstream first-byte timeout |
| `OLLAMA_QUEUE_MAX_REQUEST_BYTES` | `67108864` | Maximum JSON request size |
| `OLLAMA_QUEUE_LOG_LEVEL` | `INFO` | Python log level |

The scheduler is intentionally model-affine rather than globally fair. A busy model can continue draining while work for another model waits. This is the desired trade-off when model reloads are much more expensive than queue latency.

### Verify

```bash
curl -fsS http://127.0.0.1:11437/health
curl -fsS http://127.0.0.1:11437/api/tags

curl -fsS http://127.0.0.1:11437/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:4b-instruct","prompt":"queue smoke","stream":false,"options":{"num_predict":2}}'
```

Watch scheduling decisions with:

```bash
journalctl -u ollama-model-queue-proxy.service -f
```

### Test

```bash
python3 -m py_compile ollama_model_queue_proxy.py
python3 -m unittest discover -s tests -v
```

### License

MIT — see [LICENSE](LICENSE).
