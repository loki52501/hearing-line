# Hearing Line — agent rules

Web app that places a real phone call for a person with hearing loss: live captions with speaker
labels, typed replies spoken aloud, key facts (times, dates, addresses, numbers) pulled into a card.
Stack: Python 3.11, FastAPI, httpx, websockets. Frontend: web/index.html + vanilla JS.

## Contracts (source of truth also on codeshare.io/lkslokesh CONTRACTS section; lead edits only)
REST: POST /api/call {to, goal} -> {callId}; POST /api/say {callId, text} -> {ok}; POST /vapi/webhook
WS /ws/{callId} events: call_status{status} | caption{speaker: them|me, text, final} | fact{label, value}
  Every event is JSON with a "type" field: {"type":"caption","speaker":"them","text":"...","final":true}
Interfaces:
  telephony.start_call(to, goal) -> call_id      (async)
  telephony.say(call_id, text)                    (async)
  telephony.listen_url(call_id) -> str
  telephony.handle_webhook(payload, emit)         (async)
  speech.run_captions(call_id, listen_url, emit)  (async)
  intel.on_caption(call_id, caption, emit)        (async)
emit(event: dict) is async and publishes to that call's WebSocket.

## Ownership
lead: server/main.py, server/bus.py, server/intel.py, requirements.txt, README.md, CLAUDE.md, .env.example
telephony: server/telephony.py | speech: server/speech.py | frontend: web/
Never edit another agent's files.

## Rules
- Keys only in .env, never committed. Never log full phone numbers (mask all but last 4).
- Nothing spoken may give medical, legal or financial advice.
- Push often: git add <your files> && git commit -m "..." && git pull --rebase && git push origin main
- Mark unconfirmed API details UNVERIFIED. Blocked 3 min -> post TYPE:blocked, move on.
- Comms: codeshare.io/lkslokesh LOG, one line: [HH:MM] FROM:<you> TO:<agent|all> TYPE:status|ask|answer|blocked|done MSG:<one line>
