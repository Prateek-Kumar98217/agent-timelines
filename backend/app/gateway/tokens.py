"""Short-lived tokens that tell the gateway which branch a tool call belongs to.

The engine signs these, so the branch never comes from arguments the model can influence.
"""

import time
from collections.abc import Callable
from uuid import UUID

import jwt

from app.ports import ToolContext

ALGORITHM = "HS256"
AUDIENCE = "agent-timelines-gateway"
TOKEN_SECONDS = 120


class InvalidToolTokenError(Exception):
    pass


def mint_tool_token(
    context: ToolContext, secret: str, clock: Callable[[], float] = time.time
) -> str:
    now = int(clock())
    return jwt.encode(
        {
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + TOKEN_SECONDS,
            "branch_id": str(context.branch_id),
            "tool_call_node_id": str(context.tool_call_node_id),
            "bypass_cache": context.bypass_cache,
        },
        secret,
        algorithm=ALGORITHM,
    )


def read_tool_token(token: str, secret: str) -> ToolContext:
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM], audience=AUDIENCE)
        return ToolContext(
            branch_id=UUID(claims["branch_id"]),
            tool_call_node_id=UUID(claims["tool_call_node_id"]),
            bypass_cache=bool(claims["bypass_cache"]),
        )
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise InvalidToolTokenError("invalid tool token") from exc
