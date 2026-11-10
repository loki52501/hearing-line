"""Speech (Deepgram live captions). Owner: speech agent.

run_captions(call_id, listen_url, emit):
  Vapi listenUrl WS  --(start JSON + binary PCM16 interleaved stereo)-->  Deepgram /v1/listen (nova-3, multichannel)
  Deepgram results  --> emit({"type":"caption","speaker":"them"|"me","text":...,"final":bool})

Channel mapping (Vapi listen, per telephony + Vapi docs): ch0 = customer -> "them", ch1 = assistant -> "me".
SPEECH_SWAP_CHANNELS=1 flips it. SPEECH_EMIT_ME=0 drops "me" captions from audio (on by default so the
assistant's own lines show; the frontend dedupes them against typed replies).
Fallback: if Deepgram is unavailable, Vapi's own `transcript` JSON events on the listen socket become captions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import urlencode

import websockets

Emit = Callable[[dict], Awaitable[None]]
log = logging.getLogger("speech")

DG_URL = "wss://api.deepgram.com/v1/listen"
STALL = float(os.getenv("SPEECH_STALL_S", "1.2"))
DEFAULT_KEYTERMS = [
    "appointment", "pharmacy", "prescription", "refill", "confirmation",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]
_STOP = {"the", "and", "for", "with", "that", "this", "from", "about", "please", "call", "have", "will",
         "want", "would", "like", "need", "your", "my", "me", "to", "a", "an", "of", "on", "in", "at", "is"}

# Optional per-call goal (lets the lead/telephony feed goal words into keyterms without changing the contract).
_goals: dict[str, str] = {}
# Latency stats per call (seconds, end of audio segment -> caption emitted), finals and interims.
latency: dict[str, list[float]] = {}
latency_interim: dict[str, list[float]] = {}


def set_goal(call_id: str, goal: str) -> None:
    _goals[call_id] = goal or ""


def keyterms_for(goal: str = "", limit: int = 40) -> list[str]:
    terms = list(DEFAULT_KEYTERMS)
    for w in re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", goal or ""):
        if w.lower() not in _STOP and w.lower() not in (t.lower() for t in terms):
            terms.append(w)
    return terms[:limit]


def deepgram_url(sample_rate: int = 16000, channels: int = 2, keyterms: Optional[list[str]] = None) -> str:
    params: list[tuple[str, str]] = [
        ("model", "nova-3"),
        ("encoding", "linear16"),
        ("sample_rate", str(sample_rate)),
        ("channels", str(channels)),
        ("smart_format", "true"),
        ("interim_results", "true"),
        ("punctuate", "true"),
        ("numerals", "true"),
        ("endpointing", "300"),
        ("utterance_end_ms", "1000"),
        ("vad_events", "true"),
    ]
    if channels > 1:
        params.append(("multichannel", "true"))
    else:
        params.append(("diarize", "true"))
    for t in keyterms or []:
        params.append(("keyterm", t))
    return DG_URL + "?" + urlencode(params)


def _connect(url: str, headers: Optional[dict] = None):
    kw = {"max_size": None, "ping_interval": 20}
    if headers:
        kw["additional_headers"] = headers  # websockets >= 14
    return websockets.connect(url, **kw)


def _speaker_for_channel(ch: int) -> str:
    swap = os.getenv("SPEECH_SWAP_CHANNELS", "0") == "1"
    them_ch = 1 if swap else 0
    return "them" if ch == them_ch else "me"


def parse_dg_result(msg: dict, channels: int) -> Optional[dict]:
    """Deepgram Results message -> caption dict (or None)."""
    if msg.get("type") != "Results":
        return None
    alts = (msg.get("channel") or {}).get("alternatives") or []
    if not alts:
        return None
    text = (alts[0].get("transcript") or "").strip()
    if not text:
        return None
    if channels > 1:
        ch = (msg.get("channel_index") or [0])[0]
        speaker = _speaker_for_channel(ch)
    else:
        speaker = "them"  # mono: we cannot tell sides apart; diarization id kept in "spk"
    cap = {"type": "caption", "speaker": speaker, "text": text, "final": bool(msg.get("is_final"))}
    if channels == 1:
        words = alts[0].get("words") or []
        if words and "speaker" in words[0]:
            cap["spk"] = words[0]["speaker"]
    return cap


def caption_from_vapi(msg: dict) -> Optional[dict]:
    """Fallback: Vapi `transcript` event -> caption. role user=them (the callee), assistant=me."""
    if msg.get("type") != "transcript":
        return None
    text = (msg.get("transcript") or "").strip()
    if not text:
        return None
    speaker = "me" if msg.get("role") == "assistant" else "them"
    return {"type": "caption", "speaker": speaker, "text": text,
            "final": msg.get("transcriptType") == "final"}


async def _send(emit: Emit, cap: dict) -> None:
    if cap["speaker"] == "me" and os.getenv("SPEECH_EMIT_ME", "1") != "1":
        return
    try:
        await emit(cap)
    except Exception:
        log.exception("emit failed")


async def run_captions(call_id: str, listen_url: str, emit: Emit, goal: str = "") -> None:
    """Stream audio from listen_url to Deepgram; emit caption events
    {type:'caption', speaker:'them'|'me', text, final}."""
    dg_key = os.getenv("DEEPGRAM_API_KEY", "")
    terms = keyterms_for(goal or _goals.get(call_id, ""))
    vapi_pub = os.getenv("VAPI_PUBLIC_KEY", "")
    headers = {"Authorization": f"Bearer {vapi_pub}"} if vapi_pub else None

    # listenUrl can be slow to come alive right after call creation: retry briefly.
    vapi_ws = None
    for attempt in range(10):
        try:
            vapi_ws = await _connect(listen_url, headers)
            break
        except Exception as e:
            log.warning("listen connect %s failed (%s), retry", attempt, type(e).__name__)
            await asyncio.sleep(1.0)
    if vapi_ws is None:
        log.error("could not connect to listenUrl for %s", call_id)
        return

    sample_rate, channels = 16000, 2
    dg_ws = None
    dg_ok = asyncio.Event()
    use_vapi_fallback = not dg_key
    pending: list[bytes] = []
    stats = latency.setdefault(call_id, [])
    istats = latency_interim.setdefault(call_id, [])
    open_interim: dict[int, tuple[str, float]] = {}  # channel -> (latest interim text, when it changed)
    t0: Optional[float] = None  # wall clock when first audio byte arrived

    async def open_dg():
        nonlocal dg_ws, use_vapi_fallback
        try:
            dg_ws = await _connect(deepgram_url(sample_rate, channels, terms), {"Authorization": f"Token {dg_key}"})
            for chunk in pending:
                await dg_ws.send(chunk)
            pending.clear()
            dg_ok.set()
            log.info("deepgram connected call=%s sr=%s ch=%s keyterms=%d", call_id, sample_rate, channels, len(terms))
        except Exception as e:
            log.error("deepgram connect failed (%s); falling back to Vapi transcripts", e)
            use_vapi_fallback = True

    async def dg_reader():
        await dg_ok.wait()
        try:
            async for raw in dg_ws:
                msg = json.loads(raw)
                if msg.get("type") == "UtteranceEnd":
                    # speaker went quiet: force Deepgram to flush a final now instead of waiting.
                    await dg_ws.send(json.dumps({"type": "Finalize"}))
                    continue
                cap = parse_dg_result(msg, channels)
                if cap:
                    ch = (msg.get("channel_index") or [0])[0]
                    if cap["final"]:
                        open_interim.pop(ch, None)
                    elif cap["speaker"] == "them" and open_interim.get(ch, (None,))[0] != cap["text"]:
                        open_interim[ch] = (cap["text"], time.monotonic())
                    if t0 is not None:
                        words = ((msg.get("channel") or {}).get("alternatives") or [{}])[0].get("words") or []
                        end = words[-1]["end"] if words else (msg.get("start") or 0) + (msg.get("duration") or 0)
                        lat = round(time.monotonic() - (t0 + end), 3)
                        (stats if cap["final"] else istats).append(lat)
                    await _send(emit, cap)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("deepgram reader error")

    async def keepalive():
        # KeepAlive every 5 s; and if a channel's interim text hasn't changed for STALL s, send Finalize so the
        # final caption (and the fact card) don't wait on Deepgram's VAD (seen stalling 5 s on clean silence).
        last_ka = time.monotonic()
        while True:
            await asyncio.sleep(0.25)
            if dg_ws is None:
                continue
            try:
                now = time.monotonic()
                stalled = [ch for ch, (_, ts) in open_interim.items() if now - ts > STALL]
                if stalled:
                    for ch in stalled:
                        open_interim.pop(ch, None)
                    await dg_ws.send(json.dumps({"type": "Finalize"}))
                if now - last_ka > 5:
                    last_ka = now
                    await dg_ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                return

    reader_task = None
    ka_task = None
    try:
        if not use_vapi_fallback:
            reader_task = asyncio.create_task(dg_reader())
            ka_task = asyncio.create_task(keepalive())
        started = False
        async for frame in vapi_ws:
            if isinstance(frame, (bytes, bytearray)):
                if t0 is None:
                    t0 = time.monotonic()
                if not started:  # audio before start frame: assume defaults
                    started = True
                    if not use_vapi_fallback:
                        await open_dg()
                if use_vapi_fallback:
                    continue
                if dg_ws is None:
                    pending.append(bytes(frame))
                else:
                    try:
                        await dg_ws.send(bytes(frame))
                    except websockets.ConnectionClosed:
                        log.warning("deepgram closed; switching to Vapi transcripts")
                        use_vapi_fallback = True
                continue
            try:
                msg = json.loads(frame)
            except Exception:
                continue
            if msg.get("type") == "start" and not started:
                sample_rate = int(msg.get("sampleRate") or 16000)
                channels = int(msg.get("channels") or 2)
                started = True
                if not use_vapi_fallback:
                    await open_dg()
            elif use_vapi_fallback:
                cap = caption_from_vapi(msg)
                if cap:
                    await _send(emit, cap)
    except websockets.ConnectionClosed:
        pass
    finally:
        if dg_ws is not None:
            try:
                await dg_ws.send(json.dumps({"type": "CloseStream"}))
                if reader_task:
                    await asyncio.wait_for(reader_task, timeout=5)
            except Exception:
                pass
            await dg_ws.close()
        for t in (reader_task, ka_task):
            if t and not t.done():
                t.cancel()
        await vapi_ws.close()
        if stats:
            s = sorted(stats)
            log.info("caption latency call=%s n=%d median=%.2fs p90=%.2fs", call_id, len(s), s[len(s) // 2],
                     s[int(len(s) * 0.9) - 1 if len(s) > 1 else 0])
