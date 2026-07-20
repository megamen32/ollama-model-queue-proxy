<p align="center">
  <img src="docs/ollama-queue-banner.svg" alt="Ollama 按模型调度的队列" width="100%">
</p>

<h1 align="center">Ollama 按模型调度的队列</h1>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="README.ru.md">Русский</a> ·
  <a href="README.zh-CN.md">中文</a>
</p>

这是一个不依赖第三方库的小型 HTTP 代理。它会串行地向 Ollama 发送推理请求：当前模型已经加载时，优先完成该模型的所有等待任务，然后再切换到下一个模型。

## 工作方式

1. 第一个推理请求决定当前模型。
2. 先处理队列中属于当前模型的请求。
3. 短暂等待更多相同模型的请求。
4. 然后选择其他模型中最早进入队列的请求并切换模型。

请求会在队列中等待，不会因为另一个模型正在加载而被取消。只有达到队列容量上限时才会返回 `429`。

## 功能

- 仅使用 Python 标准库；
- 支持原生 Ollama 和 OpenAI-compatible 推理路径；
- 单个 upstream worker，配合 Ollama 设置可以保持一次只加载一个模型；
- 可配置的按模型批处理等待时间；
- `/health` 提供当前模型和队列深度；
- 输出 `queue_enqueue`、`queue_start`、`queue_switch`、`queue_done` 日志；
- 不需要修改 Ollama 本身。

## 安装

默认监听 `127.0.0.1:11437`，并转发到 `127.0.0.1:11434` 的 Ollama。

```bash
sudo install -D -m 0755 ollama_model_queue_proxy.py \
  /usr/local/libexec/ollama_model_queue_proxy.py
sudo install -D -m 0644 systemd/ollama-model-queue-proxy.service \
  /etc/systemd/system/ollama-model-queue-proxy.service
sudo systemctl daemon-reload
sudo systemctl enable --now ollama-model-queue-proxy.service
```

为了只保持一个模型加载，请同步配置 Ollama：

```ini
# /etc/systemd/system/ollama.service.d/90-queue-runtime.conf
[Service]
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_MAX_QUEUE=64"
```

然后执行 `sudo systemctl daemon-reload && sudo systemctl restart ollama`。

LiteLLM 或其他客户端应使用队列代理，而不是直接访问 Ollama 端口。LiteLLM 的 Ollama provider 请把 `api_base` 设置为 `http://127.0.0.1:11437`。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OLLAMA_QUEUE_LISTEN_HOST` | `127.0.0.1` | 监听地址 |
| `OLLAMA_QUEUE_LISTEN_PORT` | `11437` | 代理端口 |
| `OLLAMA_UPSTREAM_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `OLLAMA_MODEL_QUEUE_MAX` | `128` | 最大等待请求数 |
| `OLLAMA_QUEUE_BATCH_GRACE_S` | `0.25` | 切换前等待相同模型请求的时间 |
| `OLLAMA_PROXY_FIRST_BYTE_TIMEOUT_S` | `180` | upstream 首字节超时 |
| `OLLAMA_QUEUE_MAX_REQUEST_BYTES` | `67108864` | JSON 请求最大大小 |
| `OLLAMA_QUEUE_LOG_LEVEL` | `INFO` | 日志级别 |

调度器有意优先考虑模型局部性，而不是严格公平。如果某个模型持续有任务，其他模型可能需要等待；这样可以减少昂贵的模型重新加载。

## 验证

```bash
curl -fsS http://127.0.0.1:11437/health
curl -fsS http://127.0.0.1:11437/api/tags

curl -fsS http://127.0.0.1:11437/api/generate \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3:4b-instruct","prompt":"queue smoke","stream":false,"options":{"num_predict":2}}'
```

查看模型切换日志：

```bash
journalctl -u ollama-model-queue-proxy.service -f
```

## 测试

```bash
python3 -m py_compile ollama_model_queue_proxy.py
python3 -m unittest discover -s tests -v
```

## 许可证

MIT — 见 [LICENSE](LICENSE)。
