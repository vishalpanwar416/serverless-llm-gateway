# Serverless LLM Gateway

A small gateway that sits in front of several LLM providers and exposes one OpenAI-style endpoint, `POST /v1/chat/completions`. It's written to run on API Gateway + Lambda with DynamoDB for state, and everything can be run locally with LocalStack.

What it does:

- sends each request to the right provider based on the model name (`gpt-*` → OpenAI, `claude-*` → Anthropic, `llama*` → Ollama, and so on)
- falls back to another provider/model if the first one returns 429, a 5xx, or times out (but not on a 400; that's the caller's problem)
- checks a gateway API key on every request and keeps per-key token totals in DynamoDB
- caches responses for `temperature: 0` requests
- logs one JSON line per request

I used Ollama as the default/fallback provider since it's free and runs locally. The Ollama URL and model are set through env vars, and OpenAI/Anthropic work as soon as you add a key.

## Running it

You only need Docker. Mock mode is on by default, so you don't need any LLM keys:

```bash
docker compose up --build
```

That starts LocalStack (DynamoDB only) and the gateway on port 8080. On startup the gateway creates its two tables and adds a demo key, `sk-gw-local-demo-key`, which is only for local testing.

```bash
curl -s http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-local-demo-key" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "gpt-4o-mini",
        "messages": [
          {"role": "system", "content": "You are a helpful assistant."},
          {"role": "user", "content": "Explain serverless cold starts in one sentence."}
        ],
        "temperature": 0.7
      }'
```

Response:

```json
{
  "id": "req_31e8f9d52d134a43a3cd97b8",
  "object": "chat.completion",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 25, "completion_tokens": 18, "total_tokens": 43},
  "latency_ms": 412,
  "cached": false
}
```

A few other things worth trying:

```bash
# no key -> 401
curl -i -X POST localhost:8080/v1/chat/completions -d '{}'

# bad body -> 400, and nothing is sent upstream
curl -i -X POST localhost:8080/v1/chat/completions -H "Authorization: Bearer sk-gw-local-demo-key" \
  -d '{"model":"gpt-4o-mini","messages":[]}'

# same request twice with temperature 0 -> the second one comes back with "cached": true
for i in 1 2; do curl -s -X POST localhost:8080/v1/chat/completions -H "Authorization: Bearer sk-gw-local-demo-key" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"cache me"}],"temperature":0}'; echo; done

# usage totals for the demo key
docker compose exec gateway python scripts/create_api_key.py --usage sk-gw-local-demo-key
```

To see the fallback work, you can make the mock OpenAI provider fail on purpose:

```bash
MOCK_FAILURES=openai=429 docker compose up --build
```

Requests for `gpt-4o-mini` will then be answered by Ollama, and the response headers show `x-gateway-fallback: openai->ollama`. You can also try `openai=500`, `openai=timeout`, or `openai=400`. With 400 nothing falls back and you just get the error.

### With a real model (Ollama)

```bash
# runs Ollama in a container and pulls llama3.2:1b the first time
MOCK_MODE=false docker compose --profile ollama up --build

# or, if Ollama is already running on your machine (start it with OLLAMA_HOST=0.0.0.0)
MOCK_MODE=false OLLAMA_BASE_URL=http://host.docker.internal:11434 docker compose up --build
```

If you don't set OpenAI or Anthropic keys, those providers count as unavailable and their requests fall back to Ollama. The first Ollama call can take a while because the model has to load, so Ollama gets a longer timeout (30s) than the hosted APIs (5s).

### Without docker compose

```bash
make install      # venv + dev dependencies
make test
make localstack   # LocalStack with plain docker run
make run          # create tables + start the server on :8080
```

## How it's put together

```mermaid
flowchart LR
    C[Client] -->|POST /v1/chat/completions| AG[API Gateway]
    AG --> H[Lambda handler]
    H --> A[auth: look up key hash]
    A --> V[validate body]
    V --> K{temperature 0?}
    K -->|yes| CA[(cache)]
    CA -->|hit| R[response]
    K -->|no / miss| RT[router]
    RT --> P1[OpenAI]
    RT --> P2[Anthropic]
    RT --> P3[Ollama]
    RT -.429 / 5xx / timeout: try next.-> RT
    RT --> U[update usage counters]
    U --> R
    A --- DK[(DynamoDB: keys + usage)]
    U --- DK
    CA --- DC[(DynamoDB: cache)]
```

The code is in `src/gateway/`:

- `handler.py`: Lambda entry point. Converts the API Gateway event and calls the service. The boto3 client, providers and HTTP pool are created here at import time so warm invocations reuse them.
- `service.py`: the request flow (auth → validate → cache → route → record usage). It doesn't know about Lambda, which is why the same code runs behind `scripts/local_server.py` locally.
- `router.py`: picks a provider for the model and walks the fallback list.
- `providers/`: one file per provider plus `registry.py`, which is the only place that lists them all. Each adapter turns its provider's response into the same `ProviderResult`.
- `http.py`: the shared HTTP client. This is where upstream status codes get mapped to errors, and each error says whether it's worth retrying on another provider.
- `store.py`, `auth.py`, `cache.py`, `models.py`, `config.py`, `jsonlog.py`: the rest.

To add a provider such as Gemini, write `providers/gemini.py` with a `complete()` method, register it in `registry.py`, and add a prefix like `gemini-=gemini` to `MODEL_PREFIX_ROUTES`. Nothing else needs to change.

## Notes on some of the choices

**When to fall back.** I only fall back on errors that look like the provider's fault: 429, 5xx (including Anthropic's 529), timeouts, connection errors, or no key configured. A 400 from upstream means the request itself is wrong, so sending it to a different provider would just fail again and cost money. Upstream 401/403 means *my* key for that provider is wrong. I return that as a 502 instead of quietly falling back, otherwise a broken key could go unnoticed for a long time.

**Timeouts.** Each provider gets a 5s read timeout by default (configurable per provider). The Lambda timeout is 29s, so a slow primary plus a fallback still fits inside API Gateway's 30s limit.

**Usage counters.** Usage is updated with a single `UpdateItem` using `ADD`, so DynamoDB does the increment itself. Two requests with the same key at the same moment can't overwrite each other the way a read-then-write would. There's a test that runs 40 updates in parallel and checks the totals. The update also has `attribute_exists(key_hash)`, so a late write can't bring a deleted key back.

**API keys.** Only the SHA-256 of each key is stored, and lookups go by the hash. Logs show a masked version (`sk-g****-key`). Unknown keys and disabled keys get the same 401 message.

**If DynamoDB hiccups after the LLM call.** By that point the completion is already done (and paid for), so I still return it and log an error instead of turning it into a 500. In a real deployment I'd alert on that log line.

**Cache.** The key is a SHA-256 of model + messages + max_tokens, and only for `temperature: 0`. Entries have a TTL, but I also check expiry on read because DynamoDB can take a while to delete expired items. A cache hit counts as a request but adds no tokens, since no provider was called.

**Logs.** Each request logs one JSON line with request id, masked key, provider, model, status, latency, whether fallback was used, and token counts. Prompts and responses are never logged.

## Config

All of these are env vars (see `.env.example`):

- `MOCK_MODE`: replace every provider with a fake one (on by default in compose)
- `MOCK_FAILURES`: force failures for testing, e.g. `openai=429,anthropic=timeout`
- `MODEL_PREFIX_ROUTES`: prefix → provider, e.g. `gpt-=openai,claude-=anthropic,llama=ollama`
- `MODEL_MAP`: exact overrides as JSON, e.g. `{"gpt-4o-mini": "anthropic"}`
- `DEFAULT_PROVIDER`: used when nothing matches (default `ollama`)
- `FALLBACK_TARGETS`: ordered list like `ollama:llama3.2:1b,openai:gpt-4o-mini`
- `UPSTREAM_TIMEOUT_SECONDS`, or per provider: `OPENAI_TIMEOUT_SECONDS`, `OLLAMA_TIMEOUT_SECONDS`, ...
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`: the base URL can point at any OpenAI-compatible API (Groq, OpenRouter, vLLM...)
- `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`
- `API_KEYS_TABLE`, `CACHE_TABLE`, `CACHE_ENABLED`, `CACHE_TTL_SECONDS`, `DYNAMODB_ENDPOINT`

## Tests

```bash
make test
```

The tests cover:

- response normalization for each provider
- routing and fallback, including the "don't fall back on 400" case and real urllib3 timeout errors
- auth (missing, malformed, unknown and disabled keys)
- the whole request flow with fake stores
- the DynamoDB code against moto, including the concurrent counter test
- parsing both API Gateway event formats

## Deploying

There's a SAM template (`template.yaml`) with the HTTP API, the Lambda, both tables and an IAM policy limited to the DynamoDB actions the code actually uses.

```bash
sam build && sam deploy --guided --parameter-overrides MockMode=false ProviderSecretArn=<secrets manager arn>
API_KEYS_TABLE=<table from outputs> python scripts/create_api_key.py --name "first key"
```

## What I'd change for production

- **Streaming.** `stream: true` isn't supported right now. API Gateway buffers Lambda responses and has a ~29s limit, so real token streaming would go through a Lambda Function URL with response streaming instead. Fallback also gets harder with streaming: once tokens have started going out, you can't switch providers.
- **Secrets.** The template reads provider keys from Secrets Manager at deploy time, which means rotating a key needs a redeploy. I'd switch to the Parameters and Secrets Lambda extension so keys are fetched and cached at runtime.
- **Semantic caching.** Exact-match caching only helps with identical prompts. A semantic cache would sit in the same place but embed the prompt and do a vector lookup. That adds maybe 20–100ms to every request, so it's only worth it if the hit rate is high, and the similarity threshold has to be strict.
- **Rate limits.** Usage is tracked but not enforced yet. A per-key, per-minute counter in DynamoDB (with a TTL) returning 429 + `Retry-After` would be the next step.
- **Skipping dead providers.** Right now every request tries the primary first, even when it's been down for a while. A simple circuit breaker would skip it and save the 5s timeout each time.
