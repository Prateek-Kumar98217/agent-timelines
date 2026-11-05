"""Runs the agent's tools and owns their cache. The MCP server exposes this over the network."""

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from app.db.session import SessionFactory
from app.domain.scratchpad import search_notes, write_note
from app.kb.search import search_kb
from app.ports import (
    Browser,
    BrowserBlockedError,
    Cache,
    Embedder,
    JsonDict,
    ToolContext,
    ToolOutcome,
)
from app.tools.calculator import CalculatorError, calculate

CACHE_VERSION = "v1"
SEARCH_RESULTS = 5
KB_RESULTS = 5
NOTE_RESULTS = 5
Handler = Callable[[ToolContext, JsonDict], str]


class UnknownToolError(ValueError):
    pass


@dataclass
class GatewayTools:
    session_factory: SessionFactory
    cache: Cache
    browser: Browser
    embedder: Embedder
    cache_ttl_seconds: int = 3600
    cache_counts: Counter[str] = field(default_factory=Counter)

    def execute(self, context: ToolContext, name: str, arguments: JsonDict) -> ToolOutcome:
        handlers: dict[str, tuple[Handler, bool]] = {
            "web_search": (self._web_search, True),
            "page_fetch": (self._page_fetch, True),
            "kb_search": (self._kb_search, True),
            "calculator": (self._calculator, False),
            "scratchpad_write": (self._scratchpad_write, False),
            "scratchpad_search": (self._scratchpad_search, False),
        }
        if name not in handlers:
            raise UnknownToolError(f"unknown tool: {name}")
        handler, cacheable = handlers[name]
        key = _cache_key(name, arguments)
        if cacheable and not context.bypass_cache:
            cached = self.cache.get(key)
            if cached is not None:
                self.cache_counts[f"{name}:hit"] += 1
                return ToolOutcome(cached)
        if cacheable:
            self.cache_counts[f"{name}:miss"] += 1
        try:
            content = handler(context, arguments)
        except (BrowserBlockedError, CalculatorError) as exc:
            # Failures aren't cached, so a later call can succeed.
            return ToolOutcome(str(exc), is_error=True)
        if cacheable:
            self.cache.set(key, content, self.cache_ttl_seconds)
        return ToolOutcome(content)

    def _web_search(self, _context: ToolContext, arguments: JsonDict) -> str:
        hits = self.browser.search(arguments["query"], SEARCH_RESULTS)
        if not hits:
            return "No results."
        return "\n\n".join(f"{hit.title}\n{hit.url}\n{hit.snippet}" for hit in hits)

    def _page_fetch(self, _context: ToolContext, arguments: JsonDict) -> str:
        page = self.browser.fetch(arguments["url"])
        return f"{page.title}\n{page.url}\n\n{page.text}"

    def _kb_search(self, _context: ToolContext, arguments: JsonDict) -> str:
        hits = search_kb(self.session_factory, self.embedder, arguments["query"], KB_RESULTS)
        if not hits:
            return "No results."
        return "\n\n".join(f"{hit.content}\nSource: {hit.url}" for hit in hits)

    def _calculator(self, _context: ToolContext, arguments: JsonDict) -> str:
        return str(calculate(arguments["expression"]))

    def _scratchpad_write(self, context: ToolContext, arguments: JsonDict) -> str:
        count = write_note(self.session_factory, self.embedder, context, arguments["text"])
        return f"Saved {count} chunk(s) to the scratchpad."

    def _scratchpad_search(self, context: ToolContext, arguments: JsonDict) -> str:
        hits = search_notes(
            self.session_factory,
            self.embedder,
            context.branch_id,
            arguments["query"],
            NOTE_RESULTS,
        )
        if not hits:
            return "The scratchpad has no notes."
        return "\n\n".join(hit.content for hit in hits)


def _cache_key(name: str, arguments: JsonDict) -> str:
    body = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f"{CACHE_VERSION}:{name}:{body}".encode()).hexdigest()
    return f"tool:{digest}"
