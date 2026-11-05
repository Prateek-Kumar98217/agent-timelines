"""FastAPI application. Handlers are thin: the rules live in `app.domain`."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.schemas import (
    BranchOut,
    ComparisonOut,
    Created,
    CreateTree,
    EventOut,
    ForkRequest,
    NodeOut,
    TreeOut,
    TreeSummary,
)
from app.api.stream import event_stream, read_events
from app.bootstrap import Runtime, get_runtime
from app.db.models import Branch, Node, NodeType, Tree
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
from app.ports import Principal


def create_app(runtime: Runtime | None = None) -> FastAPI:
    """Build the app. Tests pass a runtime, and otherwise it's created on first use."""
    app = FastAPI(title="agent-timelines")

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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/trees", status_code=201)
    def create_tree(body: CreateTree, rt: RuntimeDep, principal: PrincipalDep) -> Created:
        task = body.task.strip()
        if not task:
            raise HTTPException(422, "the task can't be blank")
        tree_id, branch_id = start_task(
            rt.deps, principal.user_id, task, TreeConfig(tools=AGENT_TOOLS)
        )
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
    def get_tree(tree_id: UUID, rt: RuntimeDep, _principal: PrincipalDep) -> TreeOut:
        with rt.session_factory() as session:
            tree = session.get(Tree, tree_id)
            if tree is None:
                raise HTTPException(404, "tree not found")
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
    def cancel(branch_id: UUID, rt: RuntimeDep, _principal: PrincipalDep) -> BranchOut:
        with rt.session_factory() as session:
            if session.get(Branch, branch_id) is None:
                raise HTTPException(404, "branch not found")
        if not cancel_branch(rt.deps, branch_id):
            raise HTTPException(409, "the branch has already finished")
        with rt.session_factory() as session:
            return BranchOut.model_validate(session.get_one(Branch, branch_id))

    @app.get("/api/compare")
    def compare(a: UUID, b: UUID, rt: RuntimeDep, _principal: PrincipalDep) -> ComparisonOut:
        with rt.session_factory() as session:
            try:
                return ComparisonOut.model_validate(compare_branches(session, a, b))
            except BranchesNotComparableError as exc:
                raise HTTPException(404, str(exc)) from exc

    @app.get("/api/trees/{tree_id}/events")
    def events(
        tree_id: UUID,
        rt: RuntimeDep,
        _principal: PrincipalDep,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> list[EventOut]:
        return [
            EventOut(seq=event.seq, type=event.type, payload=event.payload)
            for event in read_events(rt.session_factory, tree_id, after)
        ]

    @app.get("/api/trees/{tree_id}/stream")
    def stream(
        tree_id: UUID,
        rt: RuntimeDep,
        _principal: PrincipalDep,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[int | None, Header()] = None,
    ) -> StreamingResponse:
        with rt.session_factory() as session:
            if session.get(Tree, tree_id) is None:
                raise HTTPException(404, "tree not found")
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
