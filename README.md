# agent-timelines

A branching workbench for agent runs. Give a small agent a task, edit any node in its run, and compare how the resulting branches diverge.

This project is under construction.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync --all-packages --all-groups --python 3.14   # install dependencies
uv run pytest backend                               # run tests with the mock profile
docker compose up -d --build --wait api worker beat # start the local stack
uv run python scripts/demo_fork.py                  # fork a scripted run and print the tree
```

Tests and the demo need the local stack. The API is at http://127.0.0.1:8000, with interactive docs
at `/docs`.

To run models with no account, use Ollama (a GPU helps):

```sh
APP_OLLAMA_URL=http://ollama:11434 docker compose --profile ollama up -d --build --wait api worker beat ollama ollama-pull
```

Grafana has an **Agent Timelines** dashboard for latency, queue wait, rejections, cache hit rate,
tokens and branch outcomes. Its source is `infra/grafana/build_dashboard.py`. One trace in Tempo
follows a branch across the API, worker and gateway. `scripts/smoke_observability.py` checks all of
this on a running stack.

Hosted providers (Groq, Gemini, OpenRouter) use a key you save with `PUT /api/keys/{provider}`.

To test failure recovery (killing a worker mid-step and stopping the broker), start the stack with
short leases and slow steps, then run the smoke test:

```sh
APP_LEASE_SECONDS=10 APP_MOCK_LLM_LATENCY_SECONDS=1 APP_ALLOW_MOCK_LLM=true APP_DEFAULT_PROVIDER=mock APP_LOCAL_ACTIVE_BRANCHES=30 \
  docker compose up -d --build --wait api worker beat
uv run python scripts/smoke_local.py
```

Grafana is at http://127.0.0.1:3000 once the stack is running.
