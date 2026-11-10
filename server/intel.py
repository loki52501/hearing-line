"""Key-fact extraction: Jev (TypeSafe System One) judges, regex extracts. Owner: lead.

For each final caption from "them": one Jev request with one Noul per fact kind.
A kind is emitted when Jev says p > THRESHOLD and the regex for that kind finds a value.
If Jev errors or times out (2 s), fall back to regex only (INTEL_REGEX_FALLBACK=1, default).
"""
import logging
import os
import re
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("intel")
Emit = Callable[[dict], Awaitable[None]]

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")
TIMEOUT = float(os.getenv("INTEL_TIMEOUT", "2.0"))
THRESHOLD = 0.7
REGEX_FALLBACK = os.getenv("INTEL_REGEX_FALLBACK", "1") == "1"

# All question wording lives here.
QUESTIONS = {
    "time": "Does `caller_said` state a time of day the listener must remember?",
    "date": "Does `caller_said` state a date or day the listener must remember?",
    "address": "Does `caller_said` state a street address or location the listener must remember?",
    "price": "Does `caller_said` state a price or amount of money the listener must remember?",
    "reference": "Does `caller_said` state a reference, confirmation, account or phone number the listener must remember?",
}
LABELS = {"time": "Time", "date": "Date", "address": "Address", "price": "Price", "reference": "Reference #"}

_MONTHS = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAYS = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
PATTERNS = {
    "time": re.compile(r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)|\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\b(?:noon|midnight)\b", re.I),
    "date": re.compile(
        rf"\b(?:(?:{_DAYS})(?:,?\s+(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)?)?|{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
        rf"|(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)\s+of\s+{_MONTHS}|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|today|tomorrow)\b", re.I),
    "address": re.compile(r"\b\d{1,6}\s+(?:[A-Z0-9][\w'.-]*\s+){1,4}(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct|place|pl|suite|parkway|pkwy)\b\.?(?:,?\s*(?:suite|apt|unit)\s*\w+)?", re.I),
    "price": re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d[\d,]*(?:\.\d{2})?\s*(?:dollars|bucks|cents)\b", re.I),
    "reference": re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]{4,}\b|\b\d{5,}\b|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
}

_seen: dict[str, set[tuple[str, str]]] = {}


def extract(text: str) -> dict[str, str]:
    out = {}
    for kind, pat in PATTERNS.items():
        m = pat.search(text)
        if m:
            out[kind] = m.group(0).strip()
    return out


async def ask_jev(text: str) -> dict[str, float] | None:
    key = os.getenv("TYPESAFE_API_KEY") or os.getenv("JEV_API_KEY")
    if not key:
        return None
    body = {
        "model": JEV_MODEL,
        "state": {"caller_said": text},
        "questions": {k: {"type": "noul", "instructions": q} for k, q in QUESTIONS.items()},
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(JEV_URL, json=body, headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            ans = r.json()["answers"]
        return {k: float(ans[k]["noul"]) for k in QUESTIONS if k in ans}
    except Exception as e:
        log.info("jev skipped: %s", type(e).__name__)
        return None


async def on_caption(call_id: str, caption: dict, emit: Emit) -> None:
    try:
        if caption.get("speaker") != "them" or not caption.get("final"):
            return
        text = (caption.get("text") or "").strip()
        if len(text) < 3:
            return
        found = extract(text)
        if not found:
            return  # nothing extractable; skip the Jev call
        probs = await ask_jev(text)
        if probs is None and not REGEX_FALLBACK:
            return
        seen = _seen.setdefault(call_id, set())
        for kind, value in found.items():
            if probs is not None and probs.get(kind, 0.0) <= THRESHOLD:
                continue
            if (kind, value.lower()) in seen:
                continue
            seen.add((kind, value.lower()))
            await emit({"type": "fact", "label": LABELS[kind], "value": value})
    except Exception:
        log.debug("intel error", exc_info=True)
