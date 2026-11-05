"""FastAPI application. Handlers are thin: the rules live in `app.domain`."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.llm import HOSTED_PROVIDERS, OLLAMA_DEFAULT_MODEL
from app.api.schemas import (
    BranchOut,
    ComparisonOut,
    Created,
    CreateTree,
    EventOut,
    ForkRequest,
    NodeOut,
    ProviderOut,
    SaveKey,
    TreeOut,
    TreeSummary,
)
from app.api.stream import event_stream, read_events
from app.bootstrap import Runtime, get_runtime
from app.db.models import Branch, Node, NodeType, Tree, User
from app.domain.compare import BranchesNotComparableError, compare_branches
from app.domain.config import AGENT_TOOLS, TreeConfig
from app.domain.engine import cancel_branch, start_task
from app.domain.forking import (
    InvalidEditError,
    NodeNotFoundError,
    NoOpEditError,
    NotEditableError,
    NoTurnsLeftError,
    fork_from_edit,
)
from app.domain.keys import KeyStore
from app.domain.limits import AdmissionRejectedError
from app.ports import Principal

MIN_KEY_CHARS = 8
MAX_KEY_CHARS = 500


def keys_of(rt: Runtime) -> KeyStore:
    if rt.key_store is None:
        raise HTTPException(404, "this build doesn't store API keys")
    return rt.key_store


def list_providers(rt: Runtime, principal: Principal) -> list[ProviderOut]:
    saved = (
        {key.provider: key.last_four for key in rt.key_store.list(principal.user_id)}
        if rt.key_store
        else {}
    )
    listed = [
        ProviderOut(
            name=info.name,
            needs_key=True,
            available=info.name in saved,
            default_model=info.default_model,
            key_last_four=saved.get(info.name),
        )
        for info in HOSTED_PROVIDERS.values()
    ]
    if rt.settings.ollama_url:
        listed.append(
            ProviderOut(
                name="ollama",
                needs_key=False,
                available=True,
                default_model=OLLAMA_DEFAULT_MODEL,
                key_last_four=None,
            )
        )
    if rt.settings.allow_mock_llm:
        listed.append(
            ProviderOut(
                name="mock",
                needs_key=False,
                available=True,
                default_model="mock",
                key_last_four=None,
            )
        )
    return listed


def choose_provider(rt: Runtime, principal: Principal, requested: str | None) -> str:
    """Use the requested provider, then the configured default, then free APIs, then Ollama."""
    options = list_providers(rt, principal)
    available = [option.name for option in options if option.available]
    if requested is not None:
        if requested not in available:
            raise HTTPException(409, f"'{requested}' isn't available; add its key first")
        return requested
    default = rt.settings.default_provider
    if default in available:
        return str(default)
    if not available:
        raise HTTPException(409, "add an API key or enable Ollama before starting a task")
    return available[0]


def require_tree(session: Session, tree_id: UUID, principal: Principal) -> Tree:
    """Return the principal's tree. Someone else's is a 404, so its existence isn't revealed."""
    tree = session.get(Tree, tree_id)
    if tree is None or tree.owner_id != principal.user_id:
        raise HTTPException(404, "tree not found")
    return tree


def require_branch(session: Session, branch_id: UUID, principal: Principal) -> Branch:
    branch = session.get(Branch, branch_id)
    if branch is None or branch.owner_id != principal.user_id:
        raise HTTPException(404, "branch not found")
    return branch


def create_app(runtime: Runtime | None = None) -> FastAPI:
    """Build the app. Tests pass a runtime, and otherwise it's created on first use."""
    app = FastAPI(title="agent-timelines")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # The default response echoes what was submitted, which could include an API key.
        detail = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    def current_runtime() -> Runtime:
        return runtime if runtime is not None else get_runtime()

    RuntimeDep = Annotated[Runtime, Depends(current_runtime)]

    def current_principal(
        request: Request, rt: RuntimeDep, authorization: Annotated[str | None, Header()] = None
    ) -> Principal:
        token = authorization.removeprefix("Bearer ") if authorization else None
        principal = rt.adapters.auth.authenticate(token)
        if principal is None:
            raise HTTPException(401, "authentication required")
        return principal

    PrincipalDep = Annotated[Principal, Depends(current_principal)]

    @app.exception_handler(AdmissionRejectedError)
    async def admission_rejected(request: Request, exc: AdmissionRejectedError) -> JSONResponse:
        rt = current_runtime()
        rt.adapters.telemetry.record("admission.rejected", 1, {"reason": exc.reason})
        return JSONResponse(
            {"detail": str(exc), "reason": exc.reason},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )

    def limit_writes(rt: RuntimeDep, principal: PrincipalDep) -> None:
        with rt.session_factory() as session:
            kind = session.get_one(User, principal.user_id).kind
        allowed = rt.deps.limits.for_kind(kind).writes_per_minute
        decision = rt.adapters.rate_limiter.consume(f"writes:{principal.user_id}", 1, allowed, 60)
        if not decision.allowed:
            raise AdmissionRejectedError(
                "rate_limited", "too many requests", decision.retry_after_seconds
            )

    WriteLimit = Annotated[None, Depends(limit_writes)]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/providers")
    def providers(rt: RuntimeDep, principal: PrincipalDep) -> list[ProviderOut]:
        return list_providers(rt, principal)

    @app.put("/api/keys/{provider}")
    def save_key(
        provider: str, body: SaveKey, rt: RuntimeDep, principal: PrincipalDep, _limit: WriteLimit
    ) -> ProviderOut:
        store = keys_of(rt)
        if provider not in HOSTED_PROVIDERS:
            raise HTTPException(404, "this provider doesn't use a saved key")
        api_key = body.api_key.get_secret_value().strip()
        if not MIN_KEY_CHARS <= len(api_key) <= MAX_KEY_CHARS:
            raise HTTPException(422, "that doesn't look like an API key")
        store.put(principal.user_id, provider, api_key)
        return next(p for p in list_providers(rt, principal) if p.name == provider)

    @app.delete("/api/keys/{provider}", status_code=204)
    def delete_key(provider: str, rt: RuntimeDep, principal: PrincipalDep) -> None:
        if not keys_of(rt).delete(principal.user_id, provider):
            raise HTTPException(404, "no key is saved for this provider")

    @app.post("/api/trees", status_code=201)
    def create_tree(
        body: CreateTree, rt: RuntimeDep, principal: PrincipalDep, _limit: WriteLimit
    ) -> Created:
        task = body.task.strip()
        if not task:
            raise HTTPException(422, "the task can't be blank")
        provider = choose_provider(rt, principal, body.provider)
        config = TreeConfig(tools=AGENT_TOOLS, provider=provider, model=body.model)
        tree_id, branch_id = start_task(rt.deps, principal.user_id, task, config)
        return Created(tree_id=tree_id, branch_id=branch_id)

    @app.get("/api/trees")
    def list_trees(rt: RuntimeDep, principal: PrincipalDep) -> list[TreeSummary]:
        branch_count = (
            select(func.count(Branch.id)).where(Branch.tree_id == Tree.id).scalar_subquery()
        )
        with rt.session_factory() as session:
            rows = session.execute(
                select(Tree.id, Tree.created_at, Node.payload, branch_count.label("branches"))
                .join(Node, Node.tree_id == Tree.id)
                .where(Tree.owner_id == principal.user_id, Node.type == NodeType.ROOT.value)
                .order_by(Tree.created_at.desc())
            ).all()
        return [
            TreeSummary(
                id=row.id,
                task=row.payload["task"],
                created_at=row.created_at,
                branches=row.branches,
            )
            for row in rows
        ]

    @app.get("/api/trees/{tree_id}")
    def get_tree(tree_id: UUID, rt: RuntimeDep, principal: PrincipalDep) -> TreeOut:
        with rt.session_factory() as session:
            tree = require_tree(session, tree_id, principal)
            branches = session.execute(
                select(Branch).where(Branch.tree_id == tree_id).order_by(Branch.created_at)
            ).scalars()
            nodes = session.execute(
                select(Node).where(Node.tree_id == tree_id).order_by(Node.created_at, Node.seq)
            ).scalars()
            return TreeOut(
                id=tree.id,
                branches=[BranchOut.model_validate(branch) for branch in branches],
                nodes=[NodeOut.model_validate(node) for node in nodes],
                last_event_seq=tree.next_event_seq - 1,
            )

    @app.post("/api/nodes/{node_id}/fork", status_code=201)
    def fork(
        node_id: UUID, body: ForkRequest, rt: RuntimeDep, principal: PrincipalDep
    ) -> BranchOut:
        with rt.session_factory() as session:
            node = session.get(Node, node_id)
            if node is None:
                raise HTTPException(404, f"node {node_id} not found")
            require_tree(session, node.tree_id, principal)
        try:
            branch_id = fork_from_edit(rt.deps, principal.user_id, node_id, body.edit)
        except NodeNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (NotEditableError, NoTurnsLeftError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except (InvalidEditError, NoOpEditError) as exc:
            raise HTTPException(422, str(exc)) from exc
        with rt.session_factory() as session:
            return BranchOut.model_validate(session.get_one(Branch, branch_id))

    @app.post("/api/branches/{branch_id}/cancel")
    def cancel(branch_id: UUID, rt: RuntimeDep, principal: PrincipalDep) -> BranchOut:
        with rt.session_factory() as session:
            require_branch(session, branch_id, principal)
        if not cancel_branch(rt.deps, branch_id):
            raise HTTPException(409, "the branch has already finished")
        with rt.session_factory() as session:
            return BranchOut.model_validate(session.get_one(Branch, branch_id))

    @app.get("/api/compare")
    def compare(a: UUID, b: UUID, rt: RuntimeDep, principal: PrincipalDep) -> ComparisonOut:
        with rt.session_factory() as session:
            require_branch(session, a, principal)
            require_branch(session, b, principal)
            try:
                return ComparisonOut.model_validate(compare_branches(session, a, b))
            except BranchesNotComparableError as exc:
                raise HTTPException(404, str(exc)) from exc

    @app.get("/api/trees/{tree_id}/events")
    def events(
        tree_id: UUID,
        rt: RuntimeDep,
        principal: PrincipalDep,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> list[EventOut]:
        with rt.session_factory() as session:
            require_tree(session, tree_id, principal)
        return [
            EventOut(seq=event.seq, type=event.type, payload=event.payload)
            for event in read_events(rt.session_factory, tree_id, after)
        ]

    @app.get("/api/trees/{tree_id}/stream")
    def stream(
        tree_id: UUID,
        rt: RuntimeDep,
        principal: PrincipalDep,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[int | None, Header()] = None,
    ) -> StreamingResponse:
        with rt.session_factory() as session:
            require_tree(session, tree_id, principal)
        return StreamingResponse(
            event_stream(
                rt.session_factory,
                rt.adapters.live_events,
                tree_id,
                last_event_id if last_event_id is not None else after,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
