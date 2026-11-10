"""FastAPI app: REST + WS routes wiring telephony, speech, intel. Owner: lead."""
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import bus, intel, speech, telephony  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

app = FastAPI(title="Hearing Line")
_tasks: dict[str, asyncio.Task] = {}


def mask(num: str) -> str:
    return "***" + (num or "")[-4:]


def call_emit(call_id: str):
    """emit that publishes to the bus and feeds captions to intel."""
    publish = bus.emitter(call_id)

    async def emit(event: dict) -> None:
        await publish(event)
        if event.get("type") == "caption":
            try:
                await intel.on_caption(call_id, event, publish)
            except Exception:
                log.exception("intel failed")
    return emit


class CallReq(BaseModel):
    to: str
    goal: str = ""


class SayReq(BaseModel):
    callId: str
    text: str


async def _run_captions(call_id: str, goal: str = "") -> None:
    try:
        url = telephony.listen_url(call_id)
        await speech.run_captions(call_id, url, call_emit(call_id), goal=goal)
    except Exception:
        log.exception("captions task failed for %s", call_id)


@app.post("/api/call")
async def api_call(req: CallReq):
    log.info("start_call to=%s", mask(req.to))
    try:
        call_id = await telephony.start_call(req.to, req.goal)
    except Exception as e:
        log.exception("start_call failed")
        raise HTTPException(502, f"start_call failed: {e}")
    await bus.publish(call_id, {"type": "call_status", "status": "queued"})
    _tasks[call_id] = asyncio.create_task(_run_captions(call_id, req.goal))
    join = getattr(telephony, "join_url", lambda _id: None)(call_id)
    return {"callId": call_id, "joinUrl": join}


@app.post("/api/say")
async def api_say(req: SayReq):
    try:
        await telephony.say(req.callId, req.text)
    except Exception as e:
        log.exception("say failed")
        raise HTTPException(502, f"say failed: {e}")
    await bus.publish(req.callId, {"type": "caption", "speaker": "me", "text": req.text, "final": True})
    return {"ok": True}


@app.post("/vapi/webhook")
async def vapi_webhook(request: Request):
    payload = await request.json()
    msg = payload.get("message", payload)
    call_id = (msg.get("call") or {}).get("id") or payload.get("callId") or "unknown"
    try:
        await telephony.handle_webhook(payload, call_emit(call_id))
    except Exception:
        log.exception("webhook handler failed")
    return {"ok": True}


@app.websocket("/ws/{call_id}")
async def ws_call(ws: WebSocket, call_id: str):
    await ws.accept()
    await bus.subscribe(call_id, ws)
    try:
        while True:
            await ws.receive_text()  # keepalive; client messages ignored
    except WebSocketDisconnect:
        pass
    finally:
        await bus.unsubscribe(call_id, ws)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "web", html=True), name="web")
