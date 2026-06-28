"""Internet access — web search + page fetch, stdlib only (no deps, no API key).

Search goes through DuckDuckGo's HTML endpoint (a form POST; the GET form is
bot-challenged). Fetch pulls a page and reduces it to readable text. Both are
best-effort and degrade cleanly when offline — drydock targets local LLMs that
may run air-gapped, so a network failure returns a plain message, never raises.

All logic original to Drydock.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"
_DDG = "https://html.duckduckgo.com/html/"

_RE_RESULT = re.compile(r'result__a"[^>]*href="([^"]+)".*?>(.*?)</a>', re.S)
_RE_SNIPPET = re.compile(r'result__snippet"[^>]*>(.*?)</a>', re.S)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_SCRIPT = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
_RE_WS = re.compile(r"[ \t]+")
_RE_BLANKS = re.compile(r"\n\s*\n\s*\n+")


def _strip_html(s: str) -> str:
    return html.unescape(_RE_TAG.sub("", s)).strip()


class WebError(RuntimeError):
    """Network/HTTP failure reaching the internet (carries a user-facing msg)."""


def search(query: str, k: int = 5, *, timeout: float = 12.0) -> list[dict]:
    """Return up to k web results: [{title, url, snippet}]. Raises WebError on a
    network failure so the caller can show a clean offline message."""
    data = urllib.parse.urlencode({"q": query}).encode()
    req = urllib.request.Request(_DDG, data=data, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (trusted)
            body = r.read().decode("utf-8", "ignore")
    except (urllib.error.URLError, OSError) as e:
        raise WebError(f"could not reach the internet ({e})") from e

    links = _RE_RESULT.findall(body)
    snippets = _RE_SNIPPET.findall(body)
    out: list[dict] = []
    for (url, title), snip in zip(links, snippets + [""] * len(links)):
        url = html.unescape(url)
        if url.startswith("//"):
            url = "https:" + url
        # DDG sometimes wraps the target in a redirect with ?uddg=<real-url>.
        m = re.search(r"[?&]uddg=([^&]+)", url)
        if m:
            url = urllib.parse.unquote(m.group(1))
        out.append({"title": _strip_html(title), "url": url, "snippet": _strip_html(snip)})
        if len(out) >= k:
            break
    return out


def fetch(url: str, *, max_chars: int = 6000, timeout: float = 12.0) -> str:
    """Fetch a URL and return its readable text (HTML stripped). Raises WebError
    on a network/HTTP failure."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (user-supplied URL)
            ctype = r.headers.get("Content-Type", "")
            raw = r.read(4_000_000)  # cap the download
    except (urllib.error.URLError, OSError) as e:
        raise WebError(f"could not fetch {url} ({e})") from e

    text = raw.decode("utf-8", "ignore")
    if "html" in ctype.lower() or "<html" in text[:2000].lower():
        text = _RE_SCRIPT.sub(" ", text)
        text = _RE_TAG.sub(" ", text)
        text = html.unescape(text)
    text = _RE_WS.sub(" ", text)
    text = _RE_BLANKS.sub("\n\n", "\n".join(ln.strip() for ln in text.splitlines()))
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n[... truncated, {len(text) - max_chars} more chars ...]"
    return text


def format_search(query: str, results: list[dict]) -> str:
    if not results:
        return f"No web results for {query!r}."
    parts = [f"Web results for {query!r}:"]
    for i, r in enumerate(results, 1):
        parts.append(f"\n[{i}] {r['title']}\n    {r['url']}\n    {r['snippet']}")
    parts.append("\nTo read one in full, use WebFetch with its URL.")
    return "\n".join(parts)
