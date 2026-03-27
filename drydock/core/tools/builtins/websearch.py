from __future__ import annotations

from collections.abc import AsyncGenerator
import os
import urllib.parse
from typing import ClassVar, final

from pydantic import BaseModel, Field
from bs4 import BeautifulSoup

from drydock.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from drydock.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from drydock.core.types import ToolStreamEvent


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
    timeout: int = Field(default=30, description="HTTP timeout in seconds.")


class WebSearch(
    BaseTool[WebSearchArgs, WebSearchResult, WebSearchConfig, BaseToolState],
    ToolUIData[WebSearchArgs, WebSearchResult],
):
    description: ClassVar[str] = "Search the web directly without an API key to find current information."

    @classmethod
    def is_available(cls) -> bool:
        # Always available! No API key required.
        return True

    @final
    async def run(
        self, args: WebSearchArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WebSearchResult, None]:
        
        import httpx

        # Grab your proxy settings
        proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            # Respect DRYDOCK_INSECURE flag for SSL bypass
            verify_ssl = os.environ.get("DRYDOCK_INSECURE") != "1"
            async with httpx.AsyncClient(proxy=proxy_url, verify=not verify_ssl if verify_ssl else False, headers=headers) as client:
                response = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": args.query},
                    timeout=self.config.timeout
                )
                response.raise_for_status()
        except Exception as e:
            raise ToolError(f"Failed to fetch search results: {str(e)}")

        # Parse the DuckDuckGo HTML results
        soup = BeautifulSoup(response.text, "html.parser")
        results = soup.find_all("div", class_="result")

        text_parts = []
        sources = []

        # Extract the top 5 results
        for res in results[:5]:
            title_tag = res.find("a", class_="result__url")
            snippet_tag = res.find("a", class_="result__snippet")
            
            if title_tag and snippet_tag:
                title = title_tag.text.strip()
                snippet = snippet_tag.text.strip()
                raw_url = title_tag.get("href", "")
                
                # Clean up DuckDuckGo's redirect URLs
                if "uddg=" in raw_url:
                    clean_url = urllib.parse.unquote(raw_url.split("uddg=")[1].split("&")[0])
                else:
                    clean_url = raw_url

                sources.append(WebSearchSource(title=title, url=clean_url))
                text_parts.append(f"Source: {title}\nURL: {clean_url}\nInformation: {snippet}\n")

        if not text_parts:
            answer = "No relevant information found on the web."
        else:
            # We bundle the snippets into a single text block for your local model to read
            answer = "Here is the raw information retrieved from the web:\n\n" + "\n".join(text_parts)

        yield WebSearchResult(answer=answer, sources=sources)

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if event.args is None or not isinstance(event.args, WebSearchArgs):
            return ToolCallDisplay(summary="websearch")
        return ToolCallDisplay(summary=f"Searching DuckDuckGo for: '{event.args.query}'")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, WebSearchResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        return ToolResultDisplay(
            success=True, message=f"{len(event.result.sources)} web sources retrieved for the local model."
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Scraping the web..."
