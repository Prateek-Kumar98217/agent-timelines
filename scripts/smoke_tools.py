"""Run every agent tool through the real MCP gateway. web_search and page_fetch use the internet.

docker compose exec -T worker python - < scripts/smoke_tools.py
"""

from sqlalchemy import select

from app.adapters.mcp_tools import McpToolExecutor
from app.bootstrap import get_runtime
from app.db.models import Branch, Node
from app.domain.config import TreeConfig
from app.domain.engine import cancel_branch, start_task
from app.factory import LOCAL_USER_ID
from app.ports import ToolContext

runtime = get_runtime()
settings = runtime.settings
tools = McpToolExecutor(settings.gateway_url, settings.gateway_token_secret)
_, branch_id = start_task(runtime.deps, LOCAL_USER_ID, "tool smoke test", TreeConfig())
cancel_branch(runtime.deps, branch_id)
with runtime.session_factory() as session:
    assert session.get(Branch, branch_id) is not None
    node_id = session.execute(
        select(Node.id).where(Node.branch_id == branch_id, Node.type == "user_msg")
    ).scalar_one()

live = ToolContext(branch_id, node_id, bypass_cache=True)
calls = [
    ("calculator", {"expression": "8849 * 3.28084"}, "29032"),
    ("kb_search", {"query": "capital of Australia"}, "Canberra"),
    ("scratchpad_write", {"text": "The summit is 8,849 metres."}, "Saved 1"),
    ("scratchpad_search", {"query": "summit height"}, "8,849"),
    ("web_search", {"query": "height of Mount Everest"}, "http"),
    ("page_fetch", {"url": "https://example.com/"}, "Example Domain"),
    ("page_fetch", {"url": "http://postgres:5432/"}, "couldn't resolve"),
    ("page_fetch", {"url": "http://169.254.169.254/latest/meta-data/"}, "non-public"),
]
failures = 0
for name, arguments, expected in calls:
    outcome = tools.execute(live, name, arguments)
    passed = expected in outcome.content
    failures += not passed
    label = "ok  " if passed else "FAIL"
    print(f"{label} {name} {arguments}: {outcome.content[:80]!r}")
print("all tools work" if not failures else f"{failures} failed")
raise SystemExit(1 if failures else 0)
