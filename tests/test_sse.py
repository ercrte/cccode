from __future__ import annotations

import httpx
import pytest

from julycode.providers.sse import iter_sse_lines


async def collect(body: str):
    response = httpx.Response(200, content=body.encode("utf-8"))
    return [event async for event in iter_sse_lines(response)]


@pytest.mark.asyncio
async def test_data_only_event() -> None:
    events = await collect("data: hello\n\n")
    assert events[0].event is None
    assert events[0].data == "hello"


@pytest.mark.asyncio
async def test_named_multiline_event() -> None:
    events = await collect("event: update\ndata: one\ndata: two\n\n")
    assert events[0].event == "update"
    assert events[0].data == "one\ntwo"


@pytest.mark.asyncio
async def test_ignores_comments() -> None:
    events = await collect(": keepalive\ndata: ok\n\n")
    assert len(events) == 1
    assert events[0].data == "ok"


@pytest.mark.asyncio
async def test_flushes_tail_without_blank_line() -> None:
    events = await collect("event: tail\ndata: ok")
    assert events[0].event == "tail"
    assert events[0].data == "ok"

