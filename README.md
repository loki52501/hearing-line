# Hearing Line

A web app that makes a real phone call for a person with hearing loss. During the call it:

- shows live captions, labelled **Them** and **You**
- speaks the user's typed replies into the call
- pulls key facts (times, dates, addresses, prices, reference numbers) into a card

Sponsors: **Deepgram** (speech-to-text nova-3, voice aura-2), **Vapi** (the phone call),
**Jev by TypeSafe** (decides which numbers in a sentence matter).

## How it works

```
browser (web/index.html) --POST /api/call--> FastAPI (server/main.py)
                                               |-- telephony.py: Vapi outbound call (web-call fallback)
                                               |-- speech.py:    Vapi listenUrl audio -> Deepgram nova-3 (2 channels: them / me)
                                               |-- intel.py:     final "them" captions -> regex finds values, Jev keeps the ones that matter
browser <--WS /ws/{callId}-- bus.py (one in-memory pub/sub per call)
typed reply --POST /api/say--> Vapi controlUrl "say" (spoken by the Deepgram aura-2 voice)
```

WebSocket events: `call_status{status}`, `caption{speaker: them|me, text, final}`, `fact{label, value}`.

## Setup

Python 3.11 or newer.

```bash
git clone https://github.com/loki52501/hearing-line.git && cd hearing-line
pip install -r requirements.txt
cp .env.example .env    # fill in the keys; never commit .env
```

| Variable | What it is |
|---|---|
| `VAPI_API_KEY` | Vapi private key |
| `VAPI_PHONE_NUMBER_ID` | ID of a Vapi phone number (`GET https://api.vapi.ai/phone-number`) |
| `VAPI_PUBLIC_KEY` | Vapi public key (used by the web-call fallback) |
| `DEEPGRAM_API_KEY` | Deepgram key |
| `TYPESAFE_API_KEY` | TypeSafe key for Jev |
| `PUBLIC_URL` | public HTTPS URL of this server, used for Vapi webhooks |

Run the server and a tunnel:

```bash
uvicorn server.main:app --port 8000
cloudflared tunnel --url http://localhost:8000   # or: ngrok http 8000
# put the https URL it prints into PUBLIC_URL, then restart uvicorn
```

Open `http://localhost:8000`.

Optional settings: `INTEL_TIMEOUT` (Jev timeout, default 2 s), `INTEL_REGEX_FALLBACK=0` (drop facts
when Jev fails instead of using regex only), `SPEECH_SWAP_CHANNELS=1` (swap the Them/You channels),
`TELEPHONY_MODE=web` (always use a browser web call).

### Backups if the phone call fails

- **Web call:** if Vapi refuses the outbound call (for example, the free daily limit), `/api/call`
  returns a `joinUrl`. The other person opens it in any browser and the captions work the same way.
- **Scripted demo:** `POST /api/demo` plays a recorded pharmacy call through the real
  Deepgram → captions → Jev pipeline. Watch it at `/ws/{callId}`, or open the page with `?fake=1`.

## 2-minute demo script

1. **(0:00) The problem.** "Phone calls are still how you book appointments, refill prescriptions,
   and sort out bills. If you can't hear the other person, you can't make the call. Hearing Line
   makes the call for you."
2. **(0:15) Start the call.** Type a teammate's phone number and the goal "refill my prescription".
   Click **Call**. The header goes from *ringing* to *connected* and shows only the last 4 digits.
3. **(0:30) Live captions.** The teammate answers as a pharmacy: "Green Valley Pharmacy, how can I
   help?" Grey interim words show up almost at once, then the full sentence turns yellow under
   **Them**. Deepgram nova-3 hears each side on its own channel, so speaker labels are always right.
4. **(0:50) Reply by typing.** Type "Hi, I'd like to refill my prescription" and press Enter, or use a
   quick reply (Alt+1–5). The Deepgram aura-2 voice says it on the call, and it appears in cyan
   under **You**.
5. **(1:10) Key facts.** The teammate says "It's ready Tuesday at 3:30 PM, confirmation 48271, we're
   at 125 Main Street, copay is $15." The facts card fills in: Time, Date, Reference #, Address, Price.
6. **(1:30) Why Jev.** "Regex finds every number. Jev decides which ones matter. 'How can I help you
   today' has the word *today*, but Jev scores it 0.01, so it stays off the card. A real date
   scores 0.98." (See the "help you today" test case in `server/intel.py`.)
7. **(1:45) Wrap up.** End the call. "Deepgram hears both sides, Vapi places the call, and Jev keeps
   the card down to what matters. Nothing the app says gives medical, legal or financial advice. It
   only repeats what you type."

## Rules for contributors

See `CLAUDE.md`. Keys live only in `.env`. Logs never show full phone numbers.
