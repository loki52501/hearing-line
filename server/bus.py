"""In-memory pub/sub per call_id with WebSocket fan-out. Owner: lead."""
import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

log = logging.getLogger("bus")

_subs: dict[str, set[WebSocket]] = defaultdict(set)
_history: dict[str, list[dict]] = defaultdict(list)  # replay for late joiners
_lock = asyncio.Lock()


async def subscribe(call_id: str, ws: WebSocket) -> None:
    async with _lock:
        _subs[call_id].add(ws)
        backlog = list(_history[call_id])
    for ev in backlog:
        await ws.send_json(ev)


async def unsubscribe(call_id: str, ws: WebSocket) -> None:
    async with _lock:
        _subs[call_id].discard(ws)


async def publish(call_id: str, event: dict) -> None:
    async with _lock:
        _history[call_id].append(event)
        if len(_history[call_id]) > 500:
            _history[call_id] = _history[call_id][-500:]
        targets = list(_subs[call_id])
    dead = []
    for ws in targets:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    if dead:
        async with _lock:
            for ws in dead:
                _subs[call_id].discard(ws)


def emitter(call_id: str):
    async def emit(event: dict) -> None:
        await publish(call_id, event)
    return emit
