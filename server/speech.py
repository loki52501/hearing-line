"""Speech (Deepgram live captions). Owner: speech agent. Stubs per CONTRACTS."""
from typing import Awaitable, Callable

Emit = Callable[[dict], Awaitable[None]]


async def run_captions(call_id: str, listen_url: str, emit: Emit) -> None:
    """Stream audio from listen_url to Deepgram; emit caption events
    {type:'caption', speaker:'them'|'me', text, final}."""
    return None
