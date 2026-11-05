"""Server-sent events for one tree.

The event log is the source of truth, and pub/sub only says when to look. Any gap in the live
stream, or a quiet period, triggers a read from the log, so a dropped message can't lose an event.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.db.models import TreeEventRow
from app.db.session import SessionFactory
from app.ports import LiveEvents, TreeEvent


def read_events(
    session_factory: SessionFactory, tree_id: UUID, after: int, limit: int = 500
) -> list[TreeEvent]:
    with session_factory() as session:
        rows = session.execute(
            select(TreeEventRow)
            .where(TreeEventRow.tree_id == tree_id, TreeEventRow.seq > after)
            .order_by(TreeEventRow.seq)
            .limit(limit)
        ).scalars()
        return [TreeEvent(tree_id, row.seq, row.type, row.payload) for row in rows]


def format_event(event: TreeEvent) -> str:
    return f"id: {event.seq}\nevent: {event.type}\ndata: {json.dumps(event.payload)}\n\n"


async def event_stream(
    session_factory: SessionFactory,
    live_events: LiveEvents,
    tree_id: UUID,
    after: int,
    poll_seconds: float = 5.0,
) -> AsyncIterator[str]:
    signals: asyncio.Queue[TreeEvent] = asyncio.Queue()

    async def pump() -> None:
        async for event in live_events.subscribe(tree_id):
            signals.put_nowait(event)

    pump_task = asyncio.create_task(pump())
    last = after
    try:
        while True:
            events = await run_in_threadpool(read_events, session_factory, tree_id, last)
            for event in events:
                yield format_event(event)
                last = event.seq
            if len(events) == 500:
                continue
            try:
                await asyncio.wait_for(signals.get(), timeout=poll_seconds)
            except TimeoutError:
                yield ": keep-alive\n\n"
    finally:
        pump_task.cancel()
