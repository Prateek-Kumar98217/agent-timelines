"""Exercise each key dependency on the current interpreter and report what works.

Run with: uv run python scripts/check_runtime.py [--with-model] [--with-browser]
--with-model downloads the embedding model; --with-browser needs Chromium installed.
"""

import argparse
import os
import subprocess
import sys
import traceback
from collections.abc import Callable

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def check_fastapi() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    api = FastAPI()
    api.get("/ping")(lambda: {"ok": True})
    assert TestClient(api).get("/ping").json() == {"ok": True}


def check_langgraph() -> None:
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class State(TypedDict):
        n: int

    graph = StateGraph(State)
    graph.add_node("inc", lambda s: {"n": s["n"] + 1})
    graph.add_edge(START, "inc")
    graph.add_edge("inc", END)
    assert graph.compile().invoke({"n": 1}) == {"n": 2}


def check_llm_clients() -> None:
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI

    ChatOpenAI(model="x", api_key="k", base_url="http://localhost:1")
    ChatGoogleGenerativeAI(model="x", google_api_key="k")


def check_celery() -> None:
    from celery import Celery

    app = Celery("check", broker="memory://", backend="cache+memory://")
    app.conf.task_always_eager = True

    @app.task(name="add")
    def add(a: int, b: int) -> int:
        return a + b

    assert add.delay(2, 3).get() == 5


def check_sql() -> None:
    import pgvector.sqlalchemy
    import psycopg
    import sqlalchemy as sa
    from alembic import command  # noqa: F401

    assert psycopg.pq.version() > 0
    engine = sa.create_engine("sqlite://")
    with engine.connect() as conn:
        assert conn.execute(sa.text("select 1")).scalar() == 1
    assert pgvector.sqlalchemy.Vector(3).dim == 3


def check_redis() -> None:
    import redis

    redis.Redis.from_url("redis://localhost:1")


def check_crypto() -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key, nonce = AESGCM.generate_key(256), b"0" * 12
    box = AESGCM(key)
    assert box.decrypt(nonce, box.encrypt(nonce, b"hi", None), None) == b"hi"


def check_mcp() -> None:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("check")

    @server.tool()
    def add(a: int, b: int) -> int:
        return a + b

    assert server.streamable_http_app(stateless_http=True) is not None


def check_otel() -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    OTLPSpanExporter(endpoint="http://localhost:1/v1/traces")
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer("check").start_as_current_span("s"):
        pass
    assert len(memory.get_finished_spans()) == 1


def check_boto3() -> None:
    import boto3

    boto3.client("sqs", aws_access_key_id="x", aws_secret_access_key="y")  # noqa: S106


def check_playwright_import() -> None:
    import playwright.sync_api  # noqa: F401


def check_playwright_browser() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content("<h1>hello</h1>")
        assert page.inner_text("h1") == "hello"
        browser.close()


def check_fastembed_import() -> None:
    import fastembed  # noqa: F401


def check_fastembed_model() -> None:
    from fastembed import TextEmbedding

    vectors = list(TextEmbedding("BAAI/bge-small-en-v1.5").embed(["hello world"]))
    assert len(vectors[0]) == 384


def check_deepeval() -> None:
    from deepeval.metrics import AnswerRelevancyMetric  # noqa: F401
    from deepeval.test_case import LLMTestCase

    LLMTestCase(input="a", actual_output="b")


def check_locust() -> None:
    # Locust monkey-patches the process on import, so it needs a process of its own.
    code = "from locust import HttpUser"
    subprocess.run([sys.executable, "-W", "ignore", "-c", code], check=True, timeout=60)  # noqa: S603


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-model", action="store_true")
    parser.add_argument("--with-browser", action="store_true")
    args = parser.parse_args()

    checks: list[Callable[[], None]] = [
        check_fastapi,
        check_langgraph,
        check_llm_clients,
        check_celery,
        check_sql,
        check_redis,
        check_crypto,
        check_mcp,
        check_otel,
        check_boto3,
        check_playwright_import,
        check_fastembed_import,
        check_deepeval,
        check_locust,
    ]
    if args.with_model:
        checks.append(check_fastembed_model)
    if args.with_browser:
        checks.append(check_playwright_browser)

    print(f"Python {sys.version.split()[0]}")
    failures = 0
    for check in checks:
        name = check.__name__.removeprefix("check_")
        try:
            check()
        except Exception as exc:
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)
        else:
            print(f"ok   {name}")
    print(f"{len(checks) - failures}/{len(checks)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
