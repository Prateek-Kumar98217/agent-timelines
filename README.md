# agent-timelines

A branching workbench for agent runs. Give a small agent a short task, edit any step of its run, and
compare what happens on the new branch. What already ran never changes.

**Test results:** http://127.0.0.1:3001/report.html once the stack is running. Every result there
comes from local tests. The deployed build isn't built.

## What it does

- **Timelines:** a task runs from top to bottom as blocks you can read without opening them, with
  new steps appearing live.
- **Edit and fork:** editing a model decision, tool call or tool result starts a parallel branch.
  The task itself is fixed.
- **Compare:** two final answers side by side with the changed words marked, plus whole-path tokens,
  turns and tools.
- **A RAG for each task:** pages the agent fetches go into the task's store, and a branch can only
  search what's on its own path.
- **Continuing:** any finished branch can take another message. It uses the turns it has left, and
  gains 4 more if it has none.
- **Limits:** turn, token, branch and queue limits for each kind of user, shown in the app. Waiting
  branches use no compute.

## Running it

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
cp .env.example .env              # then choose what to enable; the file explains each setting
docker compose up -d --build      # start everything
```

- **App:** http://127.0.0.1:3001. The API is on port 8000, with docs at `/docs`.
- **Dashboards:** http://127.0.0.1:3000 (Grafana). The **Agent Timelines** dashboard covers latency,
  queue wait, refusals, cache hits, tokens and outcomes.
- **Models:** uncomment the Ollama lines in `.env` to run on your GPU, or add a Groq or Gemini key on
  the app's API keys page.

## Testing

```sh
uv sync --all-packages --all-groups --python 3.14
docker compose up -d --wait postgres redis rabbitmq
sh scripts/check.sh                      # lint, types and unit and integration tests
npm run e2e --prefix web                 # browser tests, after: API_URL=http://127.0.0.1:8001 npm run build --prefix web
```

These run by hand, and their results feed the report:

| What | Command |
|---|---|
| Failure checks on a running stack | `scripts/smoke_local.py`, `scripts/smoke_observability.py` |
| Retrieval | `evals/recall.py` |
| Edit sensitivity (hours, on a GPU) | `evals/edit_sensitivity.py`, then `evals/report.py` |
| LLM judge on 6 fixed answers | `evals/judge.py`, with `GEMINI_API_KEY` in `.env` |
| Load | `loadtest/run.py` |
| The report itself | `evals/build_report.py` |

The judge never runs in CI or on a schedule.

## How it's built

FastAPI, Celery with RabbitMQ, a small LangGraph agent with its tools behind one MCP server,
Postgres with pgvector, Redis, and OpenTelemetry into a Grafana stack. The frontend is Next.js with
React Flow. One queue message runs one step, so a waiting branch holds no process.

Known gaps are in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
