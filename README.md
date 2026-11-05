# agent-timelines

A branching workbench for agent runs. Give a small agent a task, edit any node in its run, and compare how the resulting branches diverge.

This project is under construction.

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```sh
uv sync --all-packages --all-groups --python 3.14   # install dependencies
uv run pytest backend                               # run tests with the mock profile
docker compose up -d --wait                         # start local infrastructure
```

Grafana is at http://127.0.0.1:3000 once the stack is running.
