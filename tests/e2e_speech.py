"""E2E test for server/speech.py without a real call.

1. Makes test audio with Deepgram TTS (aura, linear16 16k) -> tests/audio/them.raw, me.raw (cached).
2. Runs a fake Vapi listen WS on localhost: sends {"type":"start",...} then interleaved stereo PCM16
   in real time (20 ms frames): ch0 = "them" line, then ch1 = "me" line.
3. Runs speech.run_captions against it and prints captions + latency.
Run: python -m tests.e2e_speech   (from repo root, DEEPGRAM_API_KEY in .env)
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import websockets
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
os.environ.setdefault("SPEECH_EMIT_ME", "1")

from server import speech  # noqa: E402

AUDIO = ROOT / "tests" / "audio"
SR = 16000
LINES = {
    "them": "Hi, this is Green Valley Pharmacy. Your prescription refill is ready. "
            "We can do Tuesday at 3:30 PM, your confirmation number is 4 8 2 7 1.",
    "me": "Great, Tuesday at 3:30 works for me. Thank you.",
}


def tts(name: str, text: str) -> bytes:
    f = AUDIO / f"{name}.raw"
    if f.exists():
        return f.read_bytes()
    AUDIO.mkdir(parents=True, exist_ok=True)
    r = httpx.post(
        "https://api.deepgram.com/v1/speak",
        params={"model": "aura-2-thalia-en" if name == "them" else "aura-2-orion-en",
                "encoding": "linear16", "sample_rate": SR, "container": "none"},
        headers={"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"},
        json={"text": text}, timeout=60,
    )
    r.raise_for_status()
    f.write_bytes(r.content)
    return r.content


def interleave(left: bytes, right: bytes) -> bytes:
    n = max(len(left), len(right)) // 2
    left = left.ljust(n * 2, b"\0")
    right = right.ljust(n * 2, b"\0")
    out = bytearray(n * 4)
    for i in range(n):
        out[i * 4:i * 4 + 2] = left[i * 2:i * 2 + 2]
        out[i * 4 + 2:i * 4 + 4] = right[i * 2:i * 2 + 2]
    return bytes(out)


async def main():
    them, me = tts("them", LINES["them"]), tts("me", LINES["me"])
    gap = b"\0" * (SR * 2 // 2)  # 0.5 s
    ch0 = them + gap + b"\0" * len(me) + gap * 2
    ch1 = b"\0" * len(them) + gap + me + gap * 2
    stereo = interleave(ch0, ch1)
    frame = SR * 2 * 2 // 50  # 20 ms stereo
    sent_at = {}

    async def handler(ws):
        await ws.send(json.dumps({"type": "start", "encoding": "linear16", "container": "raw",
                                  "sampleRate": SR, "channels": 2}))
        start = time.monotonic()
        for i in range(0, len(stereo), frame):
            await ws.send(stereo[i:i + frame])
            target = start + (i + frame) / (SR * 4)
            await asyncio.sleep(max(0, target - time.monotonic()))
        sent_at["end"] = time.monotonic()
        await asyncio.sleep(3)
        await ws.close()

    t_first = {}

    async def emit(ev):
        now = time.monotonic()
        t_first.setdefault(ev["speaker"], now)
        print(f"{now - t0:6.2f}s {ev['speaker']:4} {'FINAL' if ev['final'] else 'inter'} {ev['text']}", flush=True)

    async with websockets.serve(handler, "127.0.0.1", 8765):
        t0 = time.monotonic()
        await speech.run_captions("test", "ws://127.0.0.1:8765/listen", emit,
                                  goal="refill my prescription at Green Valley Pharmacy")
    them_end = len(them) / (SR * 2)
    print(f"them speech ends at {them_end:.2f}s audio time")
    import statistics as st
    for name, d in (("final", speech.latency), ("interim", speech.latency_interim)):
        v = d.get("test") or []
        if v:
            print(f"{name} latency n={len(v)} median={st.median(v):.2f}s max={max(v):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
