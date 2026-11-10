"""Telephony (Vapi). Owner: telephony agent. Stubs per CONTRACTS."""
from typing import Awaitable, Callable

Emit = Callable[[dict], Awaitable[None]]


async def start_call(to: str, goal: str) -> str:
    """Place an outbound call; return call_id."""
    raise NotImplementedError


async def say(call_id: str, text: str) -> None:
    """Speak text into the live call."""
    raise NotImplementedError


def listen_url(call_id: str) -> str:
    """Return the websocket URL that streams the call's audio."""
    raise NotImplementedError


async def handle_webhook(payload: dict, emit: Emit) -> None:
    """Handle a Vapi server event; emit call_status etc."""
    return None
