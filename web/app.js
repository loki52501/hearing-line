// Hearing Line frontend. Vanilla JS, no build step.
// ?fake=1  -> replay sample events from fake.js (demo backup)
// ?api=http://host:8000 -> point at a backend on another origin (optional)
(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const FAKE = params.get("fake") === "1";
  const API = (params.get("api") || "").replace(/\/$/, "");
  const QUICK = ["Can you repeat that?", "Please speak slower", "One moment please", "Yes", "No"];

  const $ = (id) => document.getElementById(id);
  const capsEl = $("captions");
  const factList = $("factList");

  let callId = null;
  let ws = null;
  let wsRetries = 0;
  let ended = false;
  const interim = { them: null, me: null };   // current interim line element per speaker
  const pendingSent = [];                       // texts we showed locally, awaiting server echo

  if (FAKE) $("fakeBadge").hidden = false;

  // ---------- helpers ----------
  const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();
  const last4 = (num) => {
    const d = (num || "").replace(/\D/g, "");
    return d.length >= 4 ? "•••• " + d.slice(-4) : "••••";
  };

  function nearBottom() {
    return capsEl.scrollHeight - capsEl.scrollTop - capsEl.clientHeight < 120;
  }
  function scrollDown(force) {
    if (force || nearBottom()) capsEl.scrollTop = capsEl.scrollHeight;
  }

  function setStatus(s) {
    const st = (s || "").toLowerCase();
    const map = {
      queued: "ringing", ringing: "ringing", "in-progress": "connected", answered: "connected",
      connected: "connected", ended: "ended", completed: "ended", failed: "ended", error: "error",
    };
    const shown = map[st] || st;
    $("status").dataset.s = shown;
    $("statusText").textContent = shown === "ringing" ? "Ringing…" : shown;
    if (shown === "ended" && !ended) {
      ended = true;
      sysLine(st === "failed" ? "Call failed." : "Call ended.");
    }
  }

  function sysLine(text) {
    const wasNear = nearBottom();
    const p = document.createElement("p");
    p.className = "sys";
    p.textContent = text;
    capsEl.appendChild(p);
    scrollDown(wasNear);
  }

  function makeLine(speaker) {
    const div = document.createElement("div");
    div.className = "line " + speaker;
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = speaker === "me" ? "You" : "Them";
    const txt = document.createElement("span");
    txt.className = "txt";
    div.append(who, txt);
    return div;
  }

  // ---------- captions ----------
  function addCaption(speaker, text, final) {
    speaker = speaker === "me" ? "me" : "them";
    text = (text || "").trim();
    if (!text) return;

    // Dedupe "me" captions we already displayed when the user sent them
    // (server echo of /api/say, plus speech transcribing our own TTS audio).
    if (speaker === "me" && isOwnEcho(text)) {
      if (interim.me && final) { interim.me.remove(); interim.me = null; }
      return;
    }

    const wasNear = nearBottom();
    let el = interim[speaker];
    if (!el) {
      el = makeLine(speaker);
      capsEl.appendChild(el);
    }
    el.querySelector(".txt").textContent = text;
    if (final) {
      el.classList.remove("interim");
      el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
      interim[speaker] = null;
    } else {
      el.classList.add("interim");
      interim[speaker] = el;
    }
    scrollDown(wasNear);
  }

  function isOwnEcho(text) {
    const now = Date.now();
    while (pendingSent.length && now - pendingSent[0].t > 30000) pendingSent.shift();
    const n = norm(text);
    if (!n) return true;
    const words = n.split(" ");
    return pendingSent.some((p) => {
      if (p.n === n || (" " + p.n + " ").includes(" " + n + " ")) return true;
      const pw = new Set(p.n.split(" "));
      const hit = words.filter((w) => pw.has(w)).length;
      return words.length >= 3 && hit / words.length >= 0.8;
    });
  }

  function showSent(text) {
    // Close any in-progress "me" interim so the typed line sits after it.
    interim.me = null;
    const el = makeLine("me");
    el.querySelector(".txt").textContent = text;
    el.classList.add("flash");
    capsEl.appendChild(el);
    scrollDown(true);
  }

  // ---------- facts ----------
  function addFact(label, value) {
    if (!value) return;
    $("factsEmpty").hidden = true;
    // Same label+value already shown? just re-highlight it at the top.
    for (const li of factList.children) {
      if (li.dataset.key === norm(label) + "|" + norm(value)) { li.remove(); break; }
    }
    factList.querySelectorAll(".fact.new").forEach((f) => f.classList.remove("new"));
    const li = document.createElement("li");
    li.className = "fact new";
    li.dataset.key = norm(label) + "|" + norm(value);
    const l = document.createElement("div");
    l.className = "lbl";
    l.textContent = label || "Note";
    const v = document.createElement("div");
    v.className = "val";
    v.textContent = value;
    li.append(l, v);
    factList.prepend(li);
  }

  // ---------- events ----------
  function onEvent(ev) {
    if (!ev || typeof ev !== "object") return;
    switch (ev.type) {
      case "call_status": setStatus(ev.status); break;
      case "caption": addCaption(ev.speaker, ev.text, ev.final !== false); break;
      case "fact": addFact(ev.label, ev.value); break;
      default: console.debug("unknown event", ev);
    }
  }

  function wsUrl(id) {
    const base = API || location.origin;
    return base.replace(/^http/, "ws") + "/ws/" + encodeURIComponent(id);
  }

  function openWs(id) {
    ws = new WebSocket(wsUrl(id));
    ws.onopen = () => { wsRetries = 0; };
    ws.onmessage = (m) => {
      try { onEvent(JSON.parse(m.data)); } catch (e) { console.warn("bad ws message", m.data); }
    };
    ws.onclose = () => {
      if (ended || id !== callId) return;
      if (wsRetries++ < 5) {
        sysLine("Connection lost, reconnecting…");
        setTimeout(() => openWs(id), 1000 * wsRetries);
      } else {
        sysLine("Lost connection to the server.");
      }
    };
  }

  async function postJson(path, body) {
    const r = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      let msg = r.status + " " + r.statusText;
      try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (e) { /* ignore */ }
      throw new Error(msg);
    }
    return r.json();
  }

  // ---------- actions ----------
  async function startCall(to, goal) {
    $("setupError").textContent = "";
    $("callBtn").disabled = true;
    $("callBtn").textContent = "Calling…";
    try {
      const res = FAKE ? await window.HLFake.startCall(to, goal, onEvent) : await postJson("/api/call", { to, goal });
      callId = res.callId;
      if (!callId) throw new Error("no callId returned");
      ended = false;
      document.body.classList.add("in-call");
      $("callee").textContent = last4(to);
      $("to").value = ""; // don't keep the full number on screen
      setStatus("ringing");
      sysLine("Calling " + last4(to) + (goal ? " — " + goal : ""));
      if (!FAKE) openWs(callId);
      $("sayText").focus();
    } catch (e) {
      $("setupError").textContent = "Could not start the call: " + e.message + (FAKE ? "" : " (demo backup: add ?fake=1 to the URL)");
    } finally {
      $("callBtn").disabled = false;
      $("callBtn").textContent = "Call";
    }
  }

  async function say(text) {
    text = (text || "").trim();
    if (!text || !callId) return;
    showSent(text);
    const entry = { n: norm(text), t: Date.now() };
    pendingSent.push(entry);
    try {
      if (FAKE) await window.HLFake.say(callId, text, onEvent);
      else await postJson("/api/say", { callId, text });
    } catch (e) {
      sysLine("Not sent: “" + text + "” (" + e.message + ")");
      const i = pendingSent.indexOf(entry);
      if (i !== -1) pendingSent.splice(i, 1);
    }
  }

  function resetCall() {
    if (ws) { try { ws.close(); } catch (e) { /* ignore */ } }
    if (FAKE) window.HLFake.stop();
    ws = null; callId = null; ended = true;
    interim.them = interim.me = null;
    pendingSent.length = 0;
    capsEl.innerHTML = "";
    factList.innerHTML = "";
    $("factsEmpty").hidden = false;
    $("callee").textContent = "";
    $("status").dataset.s = "idle";
    $("statusText").textContent = "Ready";
    document.body.classList.remove("in-call");
    $("to").focus();
  }

  // ---------- wiring ----------
  $("callForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const to = $("to").value.trim();
    if (!to) { $("setupError").textContent = "Enter a phone number."; return; }
    startCall(to, $("goal").value.trim());
  });
  // Enter in the goal box submits; Shift+Enter adds a newline.
  $("goal").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("callForm").requestSubmit(); }
  });

  $("sayForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = $("sayText").value;
    $("sayText").value = "";
    say(t);
  });

  QUICK.forEach((q, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.innerHTML = "";
    b.append(document.createTextNode(q));
    const k = document.createElement("kbd");
    k.textContent = "Alt+" + (i + 1);
    b.append(k);
    b.setAttribute("aria-keyshortcuts", "Alt+" + (i + 1));
    b.addEventListener("click", () => say(q));
    $("quick").appendChild(b);
  });

  document.addEventListener("keydown", (e) => {
    if (!document.body.classList.contains("in-call")) return;
    if (e.altKey && /^[1-5]$/.test(e.key)) { e.preventDefault(); say(QUICK[+e.key - 1]); }
    else if (e.key === "/" && document.activeElement !== $("sayText")) { e.preventDefault(); $("sayText").focus(); }
  });

  let capSize = 28;
  const setCap = (n) => { capSize = Math.max(24, Math.min(56, n)); document.documentElement.style.setProperty("--cap-size", capSize + "px"); };
  $("bigger").addEventListener("click", () => setCap(capSize + 4));
  $("smaller").addEventListener("click", () => setCap(capSize - 4));
  $("newCall").addEventListener("click", resetCall);

  $("to").focus();
})();
