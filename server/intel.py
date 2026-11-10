"""Key-fact extraction (Jev/TypeSafe + regex). Owner: lead."""
from typing import Awaitable, Callable

Emit = Callable[[dict], Awaitable[None]]


async def on_caption(call_id: str, caption: dict, emit: Emit) -> None:
    """For final 'them' captions, emit {type:'fact', label, value}."""
    return None
