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
- 使用可逆的 Ollama systemd drop-in；不会修改 Ollama 数据或二进制文件。

## 安装

在使用 systemd 的 Linux 上，可以直接从 GitHub 用一条命令把队列代理安装为 Ollama 标准端口的透明替代：

```bash
curl -fsSL https://raw.githubusercontent.com/megamen32/ollama-model-queue-proxy/main/scripts/install.sh | sudo env CHANGE_PORT=TRUE bash
```

如果已经下载了仓库，也可以执行本地命令：

```bash
sudo ./scripts/install.sh
```

安装脚本会把 Ollama backend 移到 `127.0.0.1:11435`，让队列代理监听标准的 `127.0.0.1:11434`，然后重启两个服务。它只创建可逆的 `ollama.service.d` drop-in。

可以从任何机器用一条命令删除代理并把 Ollama 恢复到标准端口：

```bash
curl -fsSL https://raw.githubusercontent.com/megamen32/ollama-model-queue-proxy/main/scripts/uninstall.sh | sudo bash
```

如果已经下载了仓库：

```bash
sudo ./scripts/uninstall.sh
```

删除命令会移除代理 unit 和 drop-in，然后让 Ollama 在 `127.0.0.1:11434` 上重新启动。

LiteLLM 或其他客户端应使用 `http://127.0.0.1:11434`。LiteLLM 的 Ollama provider 请把这个地址设置为 `api_base`。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OLLAMA_QUEUE_LISTEN_HOST` | `127.0.0.1` | 监听地址 |
| `OLLAMA_QUEUE_LISTEN_PORT` | `11434` | 代理端口 |
| `OLLAMA_UPSTREAM_URL` | `http://127.0.0.1:11435` | Ollama 地址 |
| `OLLAMA_MODEL_QUEUE_MAX` | `128` | 最大等待请求数 |
| `OLLAMA_QUEUE_BATCH_GRACE_S` | `0.25` | 切换前等待相同模型请求的时间 |
| `OLLAMA_PROXY_FIRST_BYTE_TIMEOUT_S` | `180` | upstream 首字节超时 |
| `OLLAMA_QUEUE_MAX_REQUEST_BYTES` | `67108864` | JSON 请求最大大小 |
| `OLLAMA_QUEUE_LOG_LEVEL` | `INFO` | 日志级别 |

调度器有意优先考虑模型局部性，而不是严格公平。如果某个模型持续有任务，其他模型可能需要等待；这样可以减少昂贵的模型重新加载。

## 验证

```bash
curl -fsS http://127.0.0.1:11434/health
curl -fsS http://127.0.0.1:11434/api/tags

curl -fsS http://127.0.0.1:11434/api/generate \
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
