// Fake backend: replays sample events so the UI works without the server.
// Enabled with ?fake=1. Exposes window.HLFake with the same shape app.js uses.
(function () {
  "use strict";

  // [delayMs, event] — delays are relative to the previous event.
  const SCRIPT = [
    [400, { type: "call_status", status: "ringing" }],
    [1800, { type: "call_status", status: "connected" }],
    [700, { type: "caption", speaker: "them", text: "Good afternoon,", final: false }],
    [500, { type: "caption", speaker: "them", text: "Good afternoon, Riverside Dental,", final: false }],
    [600, { type: "caption", speaker: "them", text: "Good afternoon, Riverside Dental, this is Maria. How can I help?", final: true }],
    [900, { type: "caption", speaker: "me", text: "Hi, I'd like to", final: false }],
    [600, { type: "caption", speaker: "me", text: "Hi, I'd like to book a cleaning appointment, please.", final: true }],
    [1200, { type: "caption", speaker: "them", text: "Sure, let me", final: false }],
    [500, { type: "caption", speaker: "them", text: "Sure, let me check. We have an opening", final: false }],
    [700, { type: "caption", speaker: "them", text: "Sure, let me check. We have an opening on Thursday, March 12th at 3:30 PM.", final: true }],
    [300, { type: "fact", label: "Appointment", value: "Thu, Mar 12 at 3:30 PM" }],
    [1400, { type: "caption", speaker: "them", text: "We're at 418 Elm Street,", final: false }],
    [600, { type: "caption", speaker: "them", text: "We're at 418 Elm Street, suite 200. Please arrive ten minutes early.", final: true }],
    [300, { type: "fact", label: "Address", value: "418 Elm Street, Suite 200" }],
  ];

  let timers = [];

  function clear() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  function play(emit) {
    clear();
    let t = 0;
    SCRIPT.forEach(([delay, ev]) => {
      t += delay;
      timers.push(setTimeout(() => emit(Object.assign({}, ev)), t));
    });
  }

  window.HLFake = {
    // Mirrors POST /api/call + WS /ws/{callId}
    startCall(to, goal, emit) {
      play(emit);
      return Promise.resolve({ callId: "fake-" + Date.now() });
    },
    // Mirrors POST /api/say; the "server" echoes a final "me" caption, like the real one may.
    say(callId, text, emit) {
      timers.push(setTimeout(() => emit({ type: "caption", speaker: "me", text, final: true }), 300));
      timers.push(setTimeout(() => emit({ type: "caption", speaker: "them", text: "Okay,", final: false }), 1500));
      timers.push(setTimeout(() => emit({ type: "caption", speaker: "them", text: "Okay, got it. Anything else?", final: true }), 2200));
      return Promise.resolve({ ok: true });
    },
    hangup(emit) {
      clear();
      emit({ type: "call_status", status: "ended" });
    },
    stop: clear,
  };
})();
