"""Regression test: MCPHttp/MCPStdio expose startup_timeout_sec (not _seconds)."""
import pytest
from drydock.core.config._settings import MCPHttp, MCPStdio


def test_mcphttp_has_startup_timeout_sec():
    srv = MCPHttp(name="test", transport="http", url="http://localhost:9000")
    assert hasattr(srv, "startup_timeout_sec"), "MCPHttp missing startup_timeout_sec"
    assert not hasattr(srv, "startup_timeout_seconds"), (
        "startup_timeout_seconds typo must not exist"
    )
    assert srv.startup_timeout_sec == 10.0


def test_mcpstdio_has_startup_timeout_sec():
    srv = MCPStdio(name="test", transport="stdio", command="echo")
    assert hasattr(srv, "startup_timeout_sec"), "MCPStdio missing startup_timeout_sec"
    assert srv.startup_timeout_sec == 10.0
