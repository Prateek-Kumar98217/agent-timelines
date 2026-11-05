"""Rejects tasks outside the agent's scope: coding, and long-running or huge requests.

This is a deliberately simple text check that runs before any work starts. It's a fast first filter
with known gaps, not a security control. The turn and token limits are what bound a task's cost.
"""

import re

REFUSAL_CODING = (
    "this workbench handles short research, fact checking and planning tasks, not coding"
)
REFUSAL_LONG = "this task looks too long for a short run of 4 to 12 turns; try a smaller question"

_LANGUAGE = (
    r"python|javascript|typescript|java|c\+\+|c#|rust|golang|ruby|php|sql|bash|html|css"
    r"|kotlin|swift"
)
_ARTIFACT = r"code|script|program|function|class|regex|algorithm|api|app|website|query|unit tests?"
_ACTION = r"write|create|implement|build|debug|fix|refactor|generate|develop|code"
_CODING = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"```",
        rf"\b(?:{_ACTION})\b(?:\W+\w+){{0,5}}?\W+(?:{_ARTIFACT})\b",
        rf"\b(?:{_ACTION})\b(?:\W+\w+){{0,5}}?\W+(?:{_LANGUAGE})\b",
        rf"\b(?:in|using)\s+(?:{_LANGUAGE})\s*[,.?!]?\s*$",
        r"\b(?:stack trace|segfault|compile error|syntax error|null pointer|traceback)\b",
        r"^\s*(?:def |class |import |from \w+ import |#include|function \w+\(|public static )",
    )
]
_LONG = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b\d{1,3}(?:,\d{3})+\s+words\b",
        r"\b\d{4,}\s+words\b",
        r"\b(?:write|draft)\s+(?:me\s+)?(?:a|an|the)\s+(?:\w+\s+){0,2}(?:book|novel|thesis|dissertation)\b",
        r"\bevery\s+(?:country|city|language|element|planet)\b.*\b(?:in the world|all)\b",
        r"\b(?:monitor|keep checking|run forever|continuously|every (?:hour|day|minute))\b",
    )
]


def task_problem(task: str) -> str | None:
    """Explain why a task is out of scope, or return None if it's fine."""
    for pattern in _CODING:
        if pattern.search(task):
            return REFUSAL_CODING
    for pattern in _LONG:
        if pattern.search(task):
            return REFUSAL_LONG
    return None
