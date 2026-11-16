# Очередь Ollama + Watchdog

[Ollama Model Queue](../README.ru.md) отвечает за *порядок запросов*. [Ollama Request Watchdog](https://github.com/megamen32/ollama-request-watchdog-proxy/blob/main/README.ru.md) отвечает за *восстановление и ограничения контекста*. Оба инструмента работают независимо, LiteLLM не требуется.

[English guide](with-watchdog.md)

## Как соединить сервисы

```text
Приложение / необязательный LiteLLM
                 |
                 v
  +-----------------------------+
  | Model Queue          :11437 |  Группировка запросов по модели
  +--------------+--------------+
                 |
                 v
  +-----------------------------+
  | Request Watchdog     :11436 |  Ограничения, восстановление
  +--------------+--------------+
                 |
                 v
  +-----------------------------+
  | Ollama               :11434 |  Вычисление
  +-----------------------------+
```

| Компонент | Что делает | Чего не делает |
| --- | --- | --- |
| Model Queue | Планирует порядок запросов к моделям и ограничивает длину очереди | Не перезапускает Ollama |
| Request Watchdog | Проверяет доступность, перезапускает выбранную службу, ограничивает `num_ctx` | Не выбирает порядок моделей |
| Ollama | Загружает модели и выполняет запросы | Не управляет очередями приложений |

**Важно:** не создавай циклов между прокси. Очередь направляет запросы в watchdog, watchdog в настоящую Ollama. Оба прокси по умолчанию могут занимать `11437`.

## Новая установка (Linux + systemd)

Пример сохраняет Ollama на порту `11434`. Порты `11436` и `11437` должны быть свободны. Если приложения уже запущены, сначала проверь их текущую конфигурацию и адреса клиентов.

### 1. Установить watchdog и перенести на порт 11436

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

### 2. Установить очередь, не меняя адрес Ollama

```bash
git clone https://github.com/megamen32/ollama-model-queue-proxy.git
cd ollama-model-queue-proxy
sudo env CHANGE_PORT=FALSE python3 scripts/install.py
```

Очередь начнёт слушать `11437`, а Ollama останется на `11434`.

### 3. Отправлять запросы очереди в watchdog

```bash
sudo mkdir -p /etc/systemd/system/ollama-model-queue-proxy.service.d
printf '[Service]\nEnvironment="OLLAMA_UPSTREAM_URL=http://127.0.0.1:11436"\n' |
  sudo tee /etc/systemd/system/ollama-model-queue-proxy.service.d/60-watchdog-upstream.conf

sudo systemctl daemon-reload
sudo systemctl restart ollama-model-queue-proxy.service
```

### 4. Проверить оба прокси

```bash
curl -fsS http://127.0.0.1:11437/health
curl -fsS http://127.0.0.1:11436/health
curl -fsS http://127.0.0.1:11437/api/tags

journalctl -fu ollama-model-queue-proxy.service
journalctl -fu ollama-request-watchdog-proxy.service
```

Запросы `/api/embed`, `/api/embeddings`, `/api/chat` и `/api/generate` проходят через очередь. Watchdog передаёт их дальше и ограничивает `options.num_ctx` для chat/generate. Клиенты обращаются **к очереди на порту 11437**.

### Необязательный LiteLLM

```yaml
litellm_params:
  model: ollama/your-model
  api_base: http://127.0.0.1:11437
```

LiteLLM может выступать клиентом, но ни один из прокси от него не зависит.

## Очередь на стандартном порту 11434

Если выбрать `CHANGE_PORT=TRUE`, установщик очереди перенесёт Ollama на `11435`, а саму очередь установит на `11434`:

| Компонент | Слушает | Upstream |
| --- | --- | --- |
| Model Queue | `11434` | `http://127.0.0.1:11436` |
| Request Watchdog | `11436` | `http://127.0.0.1:11435` |
| Ollama | `11435` | Модель |

Настрой эти адреса явно. Не назначай двум сервисам один порт.

## Уже работающий сервер

Сначала посмотри `systemctl cat ollama-model-queue-proxy.service`, `systemctl cat ollama-request-watchdog-proxy.service` и `systemctl cat ollama.service`. У существующего развёртывания могут быть другие порты, systemd drop-in и активные клиенты. Меняй по одному маршруту и проверяй `/health`. Не запускай установщики поверх нестандартной конфигурации вслепую.
