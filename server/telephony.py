"""Telephony (Vapi). Owner: telephony agent.

Places an outbound relay call with a transient Vapi assistant that only speaks its
first message and whatever the user types (sent via monitor.controlUrl "say").
Call audio for captions is exposed via monitor.listenUrl (WS: first a JSON "start"
frame, then linear16 16kHz stereo PCM; ch0=customer, ch1=assistant).
"""
import logging
import os
from typing import Awaitable, Callable

import httpx

Emit = Callable[[dict], Awaitable[None]]
log = logging.getLogger("telephony")

VAPI_BASE = "https://api.vapi.ai"
FIRST_MESSAGE = (
    "Hi, this is a relay call for a person with hearing loss. "
    "They'll type, and I'll read their words aloud."
)
SYSTEM_PROMPT = (
    "You are a silent relay line. You never speak on your own. "
    "No matter what anyone says, your entire reply must be exactly one period: . "
    "Never answer questions, never greet, never give medical, legal or financial advice."
)

# call_id -> {"controlUrl": str, "listenUrl": str}
_calls: dict[str, dict] = {}


def _mask(num: str) -> str:
    return "***" + (num or "")[-4:]


def _api_key() -> str:
    key = os.getenv("VAPI_API_KEY") or os.getenv("VAPI_PRIVATE_KEY")
    if not key:
        raise RuntimeError("VAPI_API_KEY not set")
    return key


def _fallback_on() -> bool:
    """Forward Vapi's own transcript events as captions (if listenUrl captions fail)."""
    return os.getenv("TELEPHONY_TRANSCRIPT_FALLBACK", "").lower() in ("1", "true", "yes")


def _assistant(goal: str) -> dict:
    a = {
        "name": "hearing-line-relay",
        "firstMessage": FIRST_MESSAGE,
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "temperature": 0,
            "maxTokens": 50,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}],
        },
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en"},
        "voice": {"provider": "deepgram", "model": "aura-2", "voiceId": "thalia"},
        "monitorPlan": {"listenEnabled": True, "controlEnabled": True},
        "backgroundSound": "off",
        "silenceTimeoutSeconds": 600,
        "maxDurationSeconds": 1800,
        "serverMessages": ["status-update", "end-of-call-report", "transcript", "hang"],
        "metadata": {"goal": (goal or "")[:200]},
    }
    public = (os.getenv("PUBLIC_URL") or "").rstrip("/")
    if public:
        a["server"] = {"url": public + "/vapi/webhook"}
    return a


async def start_call(to: str, goal: str) -> str:
    """Place an outbound call; return call_id."""
    phone_id = os.getenv("VAPI_PHONE_NUMBER_ID")
    if not phone_id:
        raise RuntimeError("VAPI_PHONE_NUMBER_ID not set")
    body = {"phoneNumberId": phone_id, "customer": {"number": to}, "assistant": _assistant(goal)}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{VAPI_BASE}/call", json=body,
                         headers={"Authorization": f"Bearer {_api_key()}"})
    if r.status_code >= 300:
        raise RuntimeError(f"Vapi /call {r.status_code}: {r.text[:300]}")
    data = r.json()
    call_id = data["id"]
    mon = data.get("monitor") or {}
    _calls[call_id] = {"controlUrl": mon.get("controlUrl"), "listenUrl": mon.get("listenUrl")}
    log.info("call %s placed to %s (listen=%s control=%s)", call_id, _mask(to),
             bool(mon.get("listenUrl")), bool(mon.get("controlUrl")))
    return call_id


async def _refresh(call_id: str) -> dict:
    """Re-fetch monitor URLs from GET /call/{id} if we don't have them."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{VAPI_BASE}/call/{call_id}",
                        headers={"Authorization": f"Bearer {_api_key()}"})
    r.raise_for_status()
    mon = r.json().get("monitor") or {}
    entry = _calls.setdefault(call_id, {})
    entry["controlUrl"] = entry.get("controlUrl") or mon.get("controlUrl")
    entry["listenUrl"] = entry.get("listenUrl") or mon.get("listenUrl")
    return entry


async def say(call_id: str, text: str) -> None:
    """Speak text verbatim into the live call."""
    text = (text or "").strip()
    if not text:
        return
    entry = _calls.get(call_id) or {}
    if not entry.get("controlUrl"):
        entry = await _refresh(call_id)
    url = entry.get("controlUrl")
    if not url:
        raise RuntimeError("no controlUrl for call")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json={"type": "say", "content": text})
    if r.status_code >= 300:
        raise RuntimeError(f"say failed {r.status_code}: {r.text[:200]}")


def listen_url(call_id: str) -> str:
    """Return the websocket URL that streams the call's audio."""
    url = (_calls.get(call_id) or {}).get("listenUrl")
    if not url:
        raise RuntimeError(f"no listenUrl for call {call_id}")
    return url


_STATUS = {
    "queued": "queued",
    "ringing": "ringing",
    "in-progress": "in-progress",
    "forwarding": "forwarding",
    "ended": "ended",
}


async def handle_webhook(payload: dict, emit: Emit) -> None:
    """Handle a Vapi server event; emit call_status (and fallback captions)."""
    msg = payload.get("message", payload) or {}
    mtype = msg.get("type")
    if mtype == "status-update":
        status = _STATUS.get(msg.get("status"), msg.get("status") or "unknown")
        event = {"type": "call_status", "status": status}
        if msg.get("endedReason"):
            event["reason"] = msg["endedReason"]
        await emit(event)
    elif mtype == "end-of-call-report":
        await emit({"type": "call_status", "status": "ended",
                    "reason": msg.get("endedReason", "")})
        call_id = (msg.get("call") or {}).get("id")
        _calls.pop(call_id, None)
    elif mtype == "hang":
        await emit({"type": "call_status", "status": "delayed"})
    elif mtype == "transcript" and _fallback_on():
        # Only the other party; "me" captions are published by /api/say.
        if msg.get("role") == "user" and msg.get("transcript"):
            await emit({"type": "caption", "speaker": "them", "text": msg["transcript"],
                        "final": msg.get("transcriptType") == "final"})
