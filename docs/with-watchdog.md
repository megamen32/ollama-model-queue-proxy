# Combine Model Queue and Request Watchdog

[Русская версия](with-watchdog.ru.md)

[Ollama Model Queue](../README.md) controls *request order*. [Ollama Request Watchdog](https://github.com/megamen32/ollama-request-watchdog-proxy) controls *recovery and context limits*. Each runs independently; neither requires LiteLLM.

## How they fit together

```text
Applications / optional LiteLLM
              |
              v
  +-------------------------+
  | Model Queue      :11437 |  Keep work for one model together
  +------------+------------+
               |
               v
  +-------------------------+
  | Request Watchdog :11436 |  Limit context, recover failures
  +------------+------------+
               |
               v
  +-------------------------+
  | Ollama           :11434 |  Execute inference
  +-------------------------+
```

| Tool | Owns | Does not own |
| --- | --- | --- |
| Model Queue | Model-affine scheduling, bounded waiting queue, queue depth | Ollama restarts or `num_ctx` limits |
| Request Watchdog | Health checks, targeted service recovery, chat/generate context limits | Choosing which model goes next |
| Ollama | Loading models, executing inference | Application-level scheduling |

**Avoid port conflicts and proxy loops.** The queue points to the watchdog, which points to the real Ollama service, never back to the queue. Both proxy installers can otherwise use port `11437`.

## Fresh installation (Linux and systemd)

This example preserves the existing Ollama port `11434`. Ensure `11436` and `11437` are available. It is meant for a fresh setup; on an already customized server, inspect the existing services and client URLs before changing them.

### 1. Install Request Watchdog, then move it to port 11436

```bash
git clone https://github.com/megamen32/ollama-request-watchdog-proxy.git
cd ollama-request-watchdog-proxy
sudo ./scripts/install.sh

sudo mkdir -p /etc/systemd/system/ollama-request-watchdog-proxy.service.d
printf '[Service]\nEnvironment="OLLAMA_PROXY_PORT=11436"\nEnvironment="OLLAMA_TARGET_URL=http://127.0.0.1:11434"\n' |
  sudo tee /etc/systemd/system/ollama-request-watchdog-proxy.service.d/60-combined-ports.conf

sudo systemctl daemon-reload
sudo systemctl restart ollama-request-watchdog-proxy.service
curl -fsS http://127.0.0.1:11436/health
```

### 2. Install the queue without moving Ollama

```bash
git clone https://github.com/megamen32/ollama-model-queue-proxy.git
cd ollama-model-queue-proxy
sudo env CHANGE_PORT=FALSE python3 scripts/install.py
```

This starts Model Queue on `11437` and keeps Ollama on `11434`.

### 3. Direct Model Queue to Watchdog

```bash
sudo mkdir -p /etc/systemd/system/ollama-model-queue-proxy.service.d
printf '[Service]\nEnvironment="OLLAMA_UPSTREAM_URL=http://127.0.0.1:11436"\n' |
  sudo tee /etc/systemd/system/ollama-model-queue-proxy.service.d/60-watchdog-upstream.conf

sudo systemctl daemon-reload
sudo systemctl restart ollama-model-queue-proxy.service
```

### 4. Check the resulting request path

```bash
curl -fsS http://127.0.0.1:11437/health
curl -fsS http://127.0.0.1:11436/health
curl -fsS http://127.0.0.1:11437/api/tags

journalctl -fu ollama-model-queue-proxy.service
journalctl -fu ollama-request-watchdog-proxy.service
```

Model Queue handles Ollama inference endpoints including `/api/embed`, `/api/embeddings`, `/api/chat` and `/api/generate`. Watchdog transparently forwards requests, limiting `options.num_ctx` for chat/generate and recovering from certain upstream failures. Clients should address the **queue on 11437**, not both proxies separately.

### Optional: use LiteLLM as the client

```yaml
litellm_params:
  model: ollama/your-model
  api_base: http://127.0.0.1:11437
```

LiteLLM is optional and neither proxy is bundled into it.

## Using the standard port for the queue instead

The queue installer with `CHANGE_PORT=TRUE` makes the queue listen on `11434` and moves the Ollama backend to `11435`. In that case:

| Component | Listen | Upstream |
| --- | --- | --- |
| Model Queue | `11434` | `http://127.0.0.1:11436` |
| Request Watchdog | `11436` | `http://127.0.0.1:11435` |
| Ollama | `11435` | Inference |

Make these settings explicitly. Never let both proxies listen on the same port.

## Existing production installations

Inspect `systemctl cat ollama-model-queue-proxy.service`, `systemctl cat ollama-request-watchdog-proxy.service` and `systemctl cat ollama.service` first. An existing host may have custom drop-ins, running clients or a different Ollama service name. Keep existing routes until the new ones are validated; do not blindly run both installers over a custom installation.

For independent use, follow the installation instructions in each repository. No chain is required if you only need scheduling or only need recovery.
