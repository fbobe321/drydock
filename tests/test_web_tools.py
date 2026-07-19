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


class _FakeResp:
    def __init__(self, body: bytes, ctype: str):
        self._body = body
        self.headers = {"Content-Type": ctype}
    def read(self, *a): return self._body
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_fetch_strips_html_script_and_tags(monkeypatch):
    import urllib.request
    html_doc = (b"<html><head><script>var x=1; evil()</script><style>a{}</style></head>"
                b"<body><h1>Title</h1><p>Hello &amp; welcome</p></body></html>")
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(html_doc, "text/html"))
    out = web.fetch("example.com")
    assert "Title" in out and "Hello & welcome" in out
    assert "evil()" not in out and "<p>" not in out   # script + tags gone


def test_fetch_truncates_long_text(monkeypatch):
    import urllib.request
    big = b"<html><body>" + b"x " * 5000 + b"</body></html>"
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(big, "text/html"))
    out = web.fetch("example.com", max_chars=500)
    assert "truncated" in out and len(out) < 700


def test_format_search_empty():
    assert "No web results" in web.format_search("rare query", [])


def test_websearch_denylist_drops_matching_results(monkeypatch):
    from drydock.tools import tool_websearch
    monkeypatch.setattr(web, "search", lambda q, k=5: [
        {"url": "https://github.com/laude-institute/terminal-bench/x", "title": "sol", "snippet": ""},
        {"url": "https://docs.python.org/3/", "title": "docs", "snippet": ""},
    ])
    out = tool_websearch({"query": "q"}, {"web_denylist": ["laude-institute", "terminal-bench"]})
    assert "docs.python.org" in out
    assert "laude-institute" not in out
    assert "withheld" in out


def test_webfetch_denylist_declines_without_error():
    from drydock.tools import tool_webfetch
    out = tool_webfetch({"url": "https://github.com/laude-institute/terminal-bench/solution.sh"},
                        {"web_denylist": ["terminal-bench"]})
    assert "not available" in out
    assert "Error" not in out


def test_web_tools_no_denylist_config_unaffected(monkeypatch):
    from drydock.tools import tool_websearch
    monkeypatch.setattr(web, "search", lambda q, k=5: [
        {"url": "https://example.com", "title": "t", "snippet": "s"}])
    out = tool_websearch({"query": "q"}, {})
    assert "example.com" in out and "withheld" not in out
