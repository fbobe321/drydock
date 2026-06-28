"""Internet access: WebSearch + WebFetch tools (drydock/web.py). Network is
mocked so tests are deterministic and run offline; the offline path is tested
explicitly (drydock targets local LLMs that may be air-gapped — must degrade,
never raise)."""
from __future__ import annotations


from drydock import web
from drydock.tools import tool_websearch, tool_webfetch


def test_websearch_formats_results(monkeypatch):
    monkeypatch.setattr(web, "search", lambda q, k=5, **kw: [
        {"title": "Python 3.13", "url": "https://docs.python.org/3/whatsnew/3.13.html",
         "snippet": "new features"},
    ])
    out = tool_websearch({"query": "python 3.13 features"}, {})
    assert "Python 3.13" in out and "docs.python.org" in out and "WebFetch" in out


def test_websearch_offline_is_graceful(monkeypatch):
    def boom(*a, **k):
        raise web.WebError("could not reach the internet (offline)")
    monkeypatch.setattr(web, "search", boom)
    out = tool_websearch({"query": "anything"}, {})
    assert "unavailable" in out.lower() and "offline" in out.lower()


def test_websearch_needs_query():
    assert "needs a `query`" in tool_websearch({}, {})


def test_webfetch_returns_text(monkeypatch):
    monkeypatch.setattr(web, "fetch", lambda url, **kw: "Readable page text here.")
    out = tool_webfetch({"url": "example.com"}, {})
    assert out == "Readable page text here."


def test_webfetch_offline_is_graceful(monkeypatch):
    def boom(*a, **k):
        raise web.WebError("could not fetch (offline)")
    monkeypatch.setattr(web, "fetch", boom)
    out = tool_webfetch({"url": "https://x.com"}, {})
    assert "could not fetch" in out.lower()


def test_webfetch_needs_url():
    assert "needs a `url`" in tool_webfetch({}, {})


def test_strip_html_and_parsing_helpers():
    # The HTML→text reducer drops tags + unescapes entities.
    assert web._strip_html("<b>Hello &amp; bye</b>") == "Hello & bye"


def test_search_parses_ddg_html(monkeypatch):
    sample = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">'
        'Example A</a>'
        '<a class="result__snippet" href="x">snippet A text</a>'
    )

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return sample.encode()

    monkeypatch.setattr(web.urllib.request, "urlopen", lambda *a, **k: _Resp())
    res = web.search("q", k=5)
    assert res and res[0]["title"] == "Example A"
    assert res[0]["url"] == "https://example.com/a"   # uddg redirect unwrapped
    assert res[0]["snippet"] == "snippet A text"
