// The simulator page: speech in (Web Speech API), captions always, speech out, MCP activity.
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const captions = $("#captions");
  const input = $("#text-input");
  const mic = $("#mic-button");
  const voiceToggle = $("#voice-toggle");
  const status = $("#status");
  const log = $("#mcp-log");

  let session;
  try {
    session = sessionStorage.getItem("aawiz-session") || crypto.randomUUID();
    sessionStorage.setItem("aawiz-session", session);
  } catch (e) {
    session = "s-" + Math.random().toString(36).slice(2);
  }

  let voiceOn = true;
  let busy = false;

  function setState(s) { document.body.dataset.state = s; }

  function addTurn(who, text, safety) {
    const empty = $("#empty");
    if (empty) empty.remove();
    const div = document.createElement("div");
    div.className = "turn " + who + (safety ? " safety" : "");
    const label = document.createElement("span");
    label.className = "who";
    label.textContent = who === "user" ? "You" : "Aawiz";
    const body = document.createElement("span");
    body.className = "text";
    body.textContent = text;
    div.append(label, body);
    captions.appendChild(div);
    captions.scrollTop = captions.scrollHeight;
  }

  function addTool(call) {
    const empty = $("#log-empty");
    if (empty) empty.remove();
    const li = document.createElement("li");
    li.dataset.tool = call.tool;
    li.dataset.ok = String(call.ok);
    const head = document.createElement("div");
    head.className = "head";
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = call.tool;
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    head.append(name, time);
    const summary = document.createElement("p");
    summary.className = "tool-summary";
    summary.textContent = call.summary;
    li.append(head, summary);
    log.appendChild(li);
  }

  function speak(text) {
    if (!voiceOn || !("speechSynthesis" in window)) { setState("idle"); return; }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = "en-US";
    u.onstart = () => setState("speaking");
    u.onend = () => setState("idle");
    u.onerror = () => setState("idle");
    window.speechSynthesis.speak(u);
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true;
    addTurn("user", text);
    input.value = "";
    setState("thinking");
    status.textContent = "";
    try {
      const res = await fetch("/api/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session, text }),
      });
      const data = await res.json();
      (data.tools || []).forEach(addTool);
      const reply = data.reply || "Sorry, could you say that again?";
      addTurn("assistant", reply, data.safety);
      speak(reply);
    } catch (e) {
      addTurn("assistant", "Sorry, I couldn't reach the simulator.");
      setState("idle");
    } finally {
      busy = false;
      input.focus();
    }
  }

  $("#composer").addEventListener("submit", (e) => {
    e.preventDefault();
    send(input.value);
  });

  voiceToggle.addEventListener("click", () => {
    voiceOn = !voiceOn;
    voiceToggle.setAttribute("aria-pressed", String(voiceOn));
    voiceToggle.textContent = voiceOn ? "Voice on" : "Voice off";
    if (!voiceOn && "speechSynthesis" in window) window.speechSynthesis.cancel();
  });

  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    mic.disabled = true;
    mic.title = "Voice input needs Chrome or Edge. You can type instead.";
    status.textContent = "Voice input is not available in this browser. Typing works.";
  } else {
    const rec = new Recognition();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    let listening = false;
    rec.onresult = (event) => {
      let finalText = "";
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i];
        if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript;
      }
      input.value = finalText || interim;
      if (finalText) send(finalText);
    };
    rec.onstart = () => { listening = true; mic.setAttribute("aria-pressed", "true"); setState("listening"); };
    rec.onend = () => {
      listening = false;
      mic.setAttribute("aria-pressed", "false");
      if (document.body.dataset.state === "listening") setState("idle");
    };
    rec.onerror = (e) => { status.textContent = "Microphone: " + e.error + ". Typing still works."; };
    mic.addEventListener("click", () => {
      if (listening) { rec.stop(); return; }
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
      try { rec.start(); } catch (e) { /* already started */ }
    });
  }

  fetch("/api/config").then((r) => r.json()).then((c) => {
    $("#brain-badge").textContent = "brain: " + c.brain + (c.model ? " (" + c.model + ")" : "");
    $("#mcp-badge").textContent = "MCP: " + c.mcp_url;
    if (c.note) status.textContent = c.note;
  }).catch(() => {});

  setState("idle");
})();
