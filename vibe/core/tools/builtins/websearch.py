from __future__ import annotations

from collections.abc import AsyncGenerator
import os
from typing import TYPE_CHECKING, ClassVar, final

import mistralai
from pydantic import BaseModel, Field

from vibe.core.config import Backend
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolStreamEvent
from vibe.core.utils import get_server_url_from_api_base

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class WebSearchSource(BaseModel):
    title: str
    url: str


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1)


class WebSearchResult(BaseModel):
    answer: str
    sources: list[WebSearchSource] = Field(default_factory=list)


class WebSearchConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    timeout: int = Field(default=120, description="HTTP timeout in seconds.")
    model: str = Field(
        default="mistral-vibe-cli-with-tools",
        description="Model to use for web search.",
    )


class WebSearch(
    BaseTool[WebSearchArgs, WebSearchResult, WebSearchConfig, BaseToolState],
    ToolUIData[WebSearchArgs, WebSearchResult],
):
    description: ClassVar[str] = (
        "Search the web for current information."
    )

    @classmethod
    def is_available(cls) -> bool:
        return bool(os.getenv("MISTRAL_API_KEY"))

    @final
    async def run(
        self, args: WebSearchArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WebSearchResult, None]:
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise ToolError("MISTRAL_API_KEY environment variable not set.")

        # Pass proxy and SSL settings if configured
        import httpx
        http_client = None
        proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
        ssl_cert = os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
        if proxy_url or ssl_cert:
            import ssl as _ssl
            ssl_ctx = None
            if ssl_cert:
                ssl_ctx = _ssl.create_default_context(cafile=ssl_cert)
            http_client = httpx.AsyncClient(
                proxy=proxy_url,
                verify=ssl_ctx or (ssl_cert or True),
            )

        client = mistralai.Mistral(
            api_key=api_key,
            server_url=self._resolve_server_url(ctx),
            timeout_ms=self.config.timeout * 1000,
        )

        try:
            async with client:
                response = await client.beta.conversations.start_async(
                    model=self.config.model,
                    instructions="Always use the web_search tool to answer queries. Never answer from memory alone.",
                    tools=[{"type": "web_search"}],
                    inputs=args.query,
                    store=False,
                )

                yield self._parse_response(response)

        except mistralai.SDKError as exc:
            raise ToolError(f"Mistral API error: {exc}") from exc

    def _resolve_server_url(self, ctx: InvokeContext | None) -> str | None:
        if not ctx or not ctx.agent_manager:
            return None
        for provider in ctx.agent_manager.config.providers:
            if provider.backend == Backend.MISTRAL:
                return get_server_url_from_api_base(provider.api_base)
        return None

    def _parse_response(
        self, response: mistralai.ConversationResponse
    ) -> WebSearchResult:
        text_parts: list[str] = []
        sources: dict[str, WebSearchSource] = {}

        for entry in response.outputs:
            if not isinstance(entry, mistralai.MessageOutputEntry):
                continue
            for chunk in entry.content:
                if isinstance(chunk, mistralai.TextChunk):
                    text_parts.append(chunk.text)
                elif isinstance(chunk, mistralai.ToolReferenceChunk) and chunk.url:
                    if chunk.url not in sources:
                        sources[chunk.url] = WebSearchSource(
                            title=chunk.title, url=chunk.url
                        )

        answer = "".join(text_parts).strip()
        if not answer:
            raise ToolError("No text in agent response.")

        return WebSearchResult(answer=answer, sources=list(sources.values()))

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if event.args is None:
            return ToolCallDisplay(summary="websearch")
        if not isinstance(event.args, WebSearchArgs):
            return ToolCallDisplay(summary="websearch")
        return ToolCallDisplay(summary=f"Searching the web: '{event.args.query}'")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, WebSearchResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        return ToolResultDisplay(
            success=True, message=f"{len(event.result.sources)} sources found"
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Searching the web"
