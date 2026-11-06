"""Real LLM providers, all through their OpenAI-compatible endpoints."""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import openai
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.domain.messages import estimate_tokens
from app.ports import (
    JsonDict,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRateLimitedError,
    ProviderRefusedError,
    ProviderTransientError,
    ToolCall,
    Usage,
)

DEFAULT_RETRY_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 120.0
FORCE_FINAL_NOTE = "\n\nYou have no tools left. Give your best final answer now."
THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    base_url: str
    default_model: str
    needs_key: bool = True


HOSTED_PROVIDERS = {
    info.name: info
    for info in (
        ProviderInfo("groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
        ProviderInfo(
            "gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"
        ),
    )
}
OLLAMA_DEFAULT_MODEL = "qwen3:4b"


def to_langchain(request: LLMRequest) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    for message in request.messages:
        match message["role"]:
            case "system":
                note = FORCE_FINAL_NOTE if request.force_final else ""
                messages.append(SystemMessage(message["content"] + note))
            case "user":
                messages.append(HumanMessage(message["content"]))
            case "assistant":
                call = message.get("tool_call")
                messages.append(
                    AIMessage(
                        content=message["content"],
                        tool_calls=(
                            [
                                {
                                    "name": call["name"],
                                    "args": call["arguments"],
                                    "id": call["id"],
                                    "type": "tool_call",
                                }
                            ]
                            if call
                            else []
                        ),
                    )
                )
            case "tool":
                messages.append(
                    ToolMessage(content=message["content"], tool_call_id=message["tool_call_id"])
                )
    return messages


def to_openai_tools(tools: Sequence[JsonDict]) -> list[JsonDict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in tools
    ]


def from_langchain(response: AIMessage, request: LLMRequest) -> LLMResponse:
    usage = response.usage_metadata
    tokens = (
        Usage(usage["input_tokens"], usage["output_tokens"])
        if usage
        else Usage(estimate_tokens(request.messages), len(str(response.content)) // 4)
    )
    text = THINKING.sub("", _text_of(response.content)).strip() or None
    calls = [ToolCall(call["name"], call["args"]) for call in response.tool_calls]
    # Unparseable arguments still count as a choice. An empty call fails validation, and the model
    # sees that as a tool error it can correct on its next turn.
    calls += [ToolCall(call["name"] or "unknown", {}) for call in response.invalid_tool_calls]
    if not calls:
        return LLMResponse(usage=tokens, text=text)
    return LLMResponse(
        usage=tokens, text=text, tool_call=calls[0], dropped_tool_calls=len(calls) - 1
    )


def _text_of(content: str | list[Any]) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        part if isinstance(part, str) else part.get("text", "")
        for part in content
        if isinstance(part, str) or part.get("type") == "text"
    )


def _retry_after(error: openai.APIStatusError) -> int:
    value = error.response.headers.get("retry-after", "")
    try:
        return max(1, int(float(value)))
    except ValueError:
        return DEFAULT_RETRY_SECONDS


class OpenAICompatibleLLM:
    """Retries are off in the client, so the engine's backoff is the only retry policy."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._model = model
        self._base_url = base_url
        self._api_key = api_key

    def complete(self, request: LLMRequest) -> LLMResponse:
        chat = ChatOpenAI(
            model=self._model,
            base_url=self._base_url,
            api_key=SecretStr(self._api_key),
            max_retries=0,
            timeout=REQUEST_TIMEOUT_SECONDS,
            temperature=request.temperature,
        )
        runnable: Any = chat.bind_tools(to_openai_tools(request.tools)) if request.tools else chat
        try:
            response = runnable.invoke(to_langchain(request))
        except openai.RateLimitError as exc:
            raise ProviderRateLimitedError(_retry_after(exc)) from None
        except openai.AuthenticationError, openai.PermissionDeniedError:
            raise ProviderRefusedError("the provider rejected the API key") from None
        except openai.NotFoundError:
            raise ProviderRefusedError(f"the model '{self._model}' isn't available") from None
        except openai.BadRequestError:
            raise ProviderRefusedError(
                f"the provider refused this request ({self._model})"
            ) from None
        except openai.APIConnectionError, openai.InternalServerError, openai.APIStatusError:
            raise ProviderTransientError("the provider is unavailable") from None
        return from_langchain(response, request)


class RoutedLLM:
    """Picks the provider a tree was created with. Keys are read at call time, never stored here."""

    def __init__(
        self,
        key_lookup: Callable[[Any, str], str | None],
        ollama_url: str | None,
        mock: LLMProvider | None = None,
    ) -> None:
        self._key_lookup = key_lookup
        self._ollama_url = ollama_url
        self._mock = mock

    def complete(self, request: LLMRequest) -> LLMResponse:
        return self._resolve(request).complete(request)

    def _resolve(self, request: LLMRequest) -> LLMProvider:
        provider = request.provider
        if provider == "mock" and self._mock is not None:
            return self._mock
        if provider == "ollama" and self._ollama_url:
            model = request.model or OLLAMA_DEFAULT_MODEL
            return OpenAICompatibleLLM(f"{self._ollama_url}/v1", "ollama", model)
        info = HOSTED_PROVIDERS.get(provider or "")
        if info is None:
            raise ProviderRefusedError(f"the provider '{provider}' isn't available")
        api_key = self._key_lookup(request.owner_id, info.name)
        if api_key is None:
            raise ProviderRefusedError(f"no API key is saved for {info.name}")
        return OpenAICompatibleLLM(info.base_url, api_key, request.model or info.default_model)
