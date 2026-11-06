"""Load test against the running stack, using the mock LLM so it measures the platform.

    export APP_ALLOW_MOCK_LLM=true APP_DEFAULT_PROVIDER=mock APP_MOCK_LLM_LATENCY_SECONDS=0.5
    export APP_LOCAL_ACTIVE_BRANCHES=<cap> APP_LOCAL_TOKENS_PER_DAY=100000000
    docker compose up -d --build --wait api worker beat
    uv run --all-groups locust -f loadtest/locustfile.py --headless -H http://127.0.0.1:8000 \\
        -u 20 -r 5 -t 60s

Rejections with a 429 are expected behaviour, so they aren't counted as failures.
"""

import random

from locust import HttpUser, between, task


class Visitor(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.result_nodes: list[str] = []
        self.tree_ids: list[str] = []

    def _accept_rejections(self, response) -> None:  # type: ignore[no-untyped-def]
        if response.status_code == 429:
            response.success()

    @task(2)
    def start_tree(self) -> None:
        with self.client.post(
            "/api/trees",
            json={"task": f"Load test question {random.randint(1, 1_000_000)}"},
            catch_response=True,
            name="POST /api/trees",
        ) as response:
            self._accept_rejections(response)
            if response.status_code == 201:
                self.tree_ids.append(response.json()["tree_id"])

    @task(3)
    def fork(self) -> None:
        if not self.result_nodes:
            return
        with self.client.post(
            f"/api/nodes/{random.choice(self.result_nodes)}/fork",
            json={"edit": {"content": f"Edited result {random.randint(1, 1_000_000)}"}},
            catch_response=True,
            name="POST /api/nodes/:id/fork",
        ) as response:
            self._accept_rejections(response)

    @task(4)
    def read_tree(self) -> None:
        if not self.tree_ids:
            return
        response = self.client.get(
            f"/api/trees/{random.choice(self.tree_ids[-5:])}", name="GET /api/trees/:id"
        )
        if response.status_code == 200:
            self.result_nodes += [
                node["id"] for node in response.json()["nodes"] if node["type"] == "tool_result"
            ]
            self.result_nodes = self.result_nodes[-50:]
