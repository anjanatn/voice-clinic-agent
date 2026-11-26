(() => {
  const TELEMETRY_TOPIC = "pulsecare.telemetry";

  const el = {
    pillApi: document.getElementById("pill-api"),
    pillGroq: document.getElementById("pill-groq"),
    pillTts: document.getElementById("pill-tts"),
    pillLivekit: document.getElementById("pill-livekit"),
    ttsProviderLabel: document.getElementById("tts-provider-label"),
    roomName: document.getElementById("room-name"),
    patientName: document.getElementById("patient-name"),
    btnConnect: document.getElementById("btn-connect"),
    btnMute: document.getElementById("btn-mute"),
    btnDemo: document.getElementById("btn-demo"),
    btnReset: document.getElementById("btn-reset"),
    btnClearChat: document.getElementById("btn-clear-chat"),
    metricLastTotal: document.getElementById("metric-last-total"),
    metricModeBadge: document.getElementById("metric-mode-badge"),
    metricAvgTotal: document.getElementById("metric-avg-total"),
    metricTurnCount: document.getElementById("metric-turn-count"),
    metricLlmTtft: document.getElementById("metric-llm-ttft"),
    metricTps: document.getElementById("metric-tps"),
    metricTtsTtfb: document.getElementById("metric-tts-ttfb"),
    metricEou: document.getElementById("metric-eou"),
    metricToolLast: document.getElementById("metric-tool-last"),
    agentStateBadge: document.getElementById("agent-state-badge"),
    waveform: document.getElementById("waveform"),
    chatFeed: document.getElementById("chat-feed"),
    waterfallList: document.getElementById("waterfall-list"),
    sseIndicator: document.getElementById("sse-indicator"),
    apptCount: document.getElementById("appt-count"),
    appointmentsList: document.getElementById("appointments-list"),
    slotsList: document.getElementById("slots-list"),
    specialtyFilter: document.getElementById("specialty-filter"),
    remoteAudioContainer: document.getElementById("remote-audio-container"),
  };

  let room = null;
  let isMuted = false;
  let interimUserMsg = null;
  let lastAssistantMsgEl = null;
  const turnHistory = [];
  let latestBookingId = null;

  function setPill(node, label, ok) {
    node.textContent = label;
    node.className = `pill ${ok ? "pill-ok" : "pill-warn"}`;
  }

  async function refreshHealth() {
    try {
      const resp = await fetch("/api/health");
      const data = await resp.json();
      setPill(el.pillApi, `FastAPI: ${data.available_slots} open slots`, true);
      setPill(el.pillGroq, `Groq: ${data.groq_configured ? data.llm_model : "key needed"}`, data.groq_configured);
      setPill(
        el.pillTts,
        `${data.tts_provider.toUpperCase()}: ${data.tts_configured ? "ready" : "key needed"}`,
        data.tts_configured
      );
      setPill(el.pillLivekit, `LiveKit: ${data.livekit_configured ? "ready" : "key needed"}`, data.livekit_configured);
      el.ttsProviderLabel.textContent = data.tts_provider === "deepgram" ? "Deepgram" : "Cartesia";
    } catch {
      setPill(el.pillApi, "FastAPI: offline", false);
    }
  }

  async function refreshSchedule() {
    const specialty = el.specialtyFilter.value;
    const slotsUrl = specialty
      ? `/api/slots?specialty=${encodeURIComponent(specialty)}`
      : "/api/slots";

    const [apptsResp, slotsResp] = await Promise.all([
      fetch("/api/appointments"),
      fetch(slotsUrl),
    ]);
    const appointments = await apptsResp.json();
    const slots = await slotsResp.json();

    el.apptCount.textContent = String(appointments.length);
    el.appointmentsList.innerHTML = appointments.length
      ? appointments
          .map(
            (a) => `
          <div class="appt-item ${a.booking_id === latestBookingId ? "highlight" : ""}">
            <div>
              <div><span class="appt-id">${escapeHtml(a.booking_id)}</span> · <strong>${escapeHtml(a.patient_name)}</strong></div>
              <div class="appt-meta">${escapeHtml(a.doctor_name)} (${escapeHtml(a.specialty)}) · ${escapeHtml(a.date)} ${escapeHtml(a.start_time)}</div>
              <div class="appt-meta">Phone: ${escapeHtml(a.phone)} · ${escapeHtml(a.reason)}</div>
            </div>
            <span class="slot-tag">${escapeHtml(a.status.toUpperCase())}</span>
          </div>`
          )
          .join("")
      : `<p class="muted-note">No confirmed appointments.</p>`;

    el.slotsList.innerHTML = slots.slice(0, 12).length
      ? slots
          .slice(0, 12)
          .map(
            (s) => `
          <div class="slot-item">
            <div>
              <div><strong>${escapeHtml(s.doctor_name)}</strong> · ${escapeHtml(s.specialty)}</div>
              <div class="slot-meta">${escapeHtml(s.date)} · ${escapeHtml(s.start_time)}–${escapeHtml(s.end_time)}</div>
            </div>
            <span class="slot-tag">${escapeHtml(s.id)}</span>
          </div>`
          )
          .join("")
      : `<p class="muted-note">No open slots match filter.</p>`;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function clearEmptyState() {
    const empty = el.chatFeed.querySelector(".empty-state");
    if (empty) empty.remove();
  }

  function setAgentState(state) {
    const normalized = String(state || "idle").toLowerCase();
    el.agentStateBadge.textContent = normalized.toUpperCase();
    el.agentStateBadge.className = `state-badge state-${normalized}`;
    if (normalized === "listening" || normalized === "speaking") {
      el.waveform.classList.add("active");
    } else {
      el.waveform.classList.remove("active");
    }
  }

  function appendTranscript(role, text, isFinal = true, latencyBadge = null) {
    if (!text) return;
    clearEmptyState();

    if (role === "user" && !isFinal) {
      if (!interimUserMsg) {
        interimUserMsg = document.createElement("div");
        interimUserMsg.className = "msg msg-user msg-interim";
        interimUserMsg.innerHTML = `<div class="msg-meta">You (listening...)</div><div class="msg-bubble"></div>`;
        el.chatFeed.appendChild(interimUserMsg);
      }
      interimUserMsg.querySelector(".msg-bubble").textContent = text;
      el.chatFeed.scrollTop = el.chatFeed.scrollHeight;
      return;
    }

    if (role === "user" && isFinal && interimUserMsg) {
      interimUserMsg.remove();
      interimUserMsg = null;
    }

    const msgEl = document.createElement("div");
    msgEl.className = `msg msg-${role}`;
    const label = role === "user" ? "Patient (Voice)" : "Maya (PulseCare AI)";
    const badgeHtml = latencyBadge ? latencyBadge : "";
    msgEl.innerHTML = `
      <div class="msg-meta">
        <span>${label}</span>
        <span class="msg-badge-slot">${badgeHtml}</span>
      </div>
      <div class="msg-bubble">${escapeHtml(text)}</div>
    `;
    el.chatFeed.appendChild(msgEl);
    if (role === "assistant") {
      lastAssistantMsgEl = msgEl;
    }
    el.chatFeed.scrollTop = el.chatFeed.scrollHeight;
  }

  function appendToolCard(toolEvent) {
    clearEmptyState();
    const card = document.createElement("div");
    card.className = "tool-card";
    const statusText = toolEvent.success ? "OK" : "ERR";
    const summary = JSON.stringify(toolEvent.result, null, 2);
    card.innerHTML = `
      <div class="tool-card-header">
        <span>TOOL: ${escapeHtml(toolEvent.name)}(${escapeHtml(JSON.stringify(toolEvent.args))})</span>
        <span>${statusText} · ${toolEvent.duration_ms} ms</span>
      </div>
      <pre>${escapeHtml(summary.length > 340 ? summary.slice(0, 340) + "\n..." : summary)}</pre>
    `;
    el.chatFeed.appendChild(card);
    el.chatFeed.scrollTop = el.chatFeed.scrollHeight;
    el.metricToolLast.textContent = `Tool API (${toolEvent.name}): ${toolEvent.duration_ms} ms`;

    if (toolEvent.result && toolEvent.result.booking_id) {
      latestBookingId = toolEvent.result.booking_id;
    }
  }

  function recordLatencyTurn(turn, modeLabel = "Live WebRTC") {
    const totalMs =
      turn.perceived_total_ms !== null && turn.perceived_total_ms !== undefined
        ? turn.perceived_total_ms
        : turn.pipeline_sum_ms;
    if (totalMs == null) return;

    turnHistory.push({ ...turn, totalMs, modeLabel });
    const avg = turnHistory.reduce((acc, t) => acc + t.totalMs, 0) / turnHistory.length;

    el.metricLastTotal.innerHTML = `${Math.round(totalMs)} <small>ms</small>`;
    el.metricModeBadge.textContent = modeLabel;
    el.metricAvgTotal.innerHTML = `${Math.round(avg)} <small>ms</small>`;
    el.metricTurnCount.textContent = `${turnHistory.length} turn${turnHistory.length === 1 ? "" : "s"} measured`;

    if (turn.llm_ttft_ms != null) {
      el.metricLlmTtft.innerHTML = `${Math.round(turn.llm_ttft_ms)} <small>ms</small>`;
    }
    if (turn.tokens_per_sec != null) {
      el.metricTps.textContent = `${Math.round(turn.tokens_per_sec)} tok/s`;
    }
    if (turn.tts_ttfb_ms != null) {
      el.metricTtsTtfb.innerHTML = `${Math.round(turn.tts_ttfb_ms)} <small>ms</small>`;
    }
    if (turn.eou_ms != null) {
      el.metricEou.textContent = `EOU: ${Math.round(turn.eou_ms)} ms`;
    }

    const parts = [];
    if (turn.eou_ms != null) parts.push(`EOU ${Math.round(turn.eou_ms)}ms`);
    if (turn.llm_ttft_ms != null) parts.push(`LLM ${Math.round(turn.llm_ttft_ms)}ms`);
    if (turn.tool_total_ms > 0) parts.push(`Tool ${Math.round(turn.tool_total_ms)}ms`);
    if (turn.tts_ttfb_ms != null) parts.push(`TTS ${Math.round(turn.tts_ttfb_ms)}ms`);

    const isSim = modeLabel.toLowerCase().includes("sim");
    const badgeHtml = `<span class="latency-pill ${isSim ? "sim" : ""}">${Math.round(totalMs)} ms${
      parts.length ? " · " + parts.join(" · ") : ""
    }</span>`;

    if (lastAssistantMsgEl) {
      const slot = lastAssistantMsgEl.querySelector(".msg-badge-slot");
      if (slot && !slot.innerHTML) {
        slot.innerHTML = badgeHtml;
      }
    }

    renderWaterfall();
  }

  function renderWaterfall() {
    if (!turnHistory.length) {
      el.waterfallList.innerHTML = `<p class="muted-note">No turns recorded yet.</p>`;
      return;
    }
    const maxTotal = Math.max(...turnHistory.map((t) => t.totalMs), 500);
    el.waterfallList.innerHTML = turnHistory
      .slice(-6)
      .map((t) => {
        const eou = t.eou_ms || 0;
        const llm = t.llm_ttft_ms || 0;
        const tool = t.tool_total_ms || 0;
        const tts = t.tts_ttfb_ms || 0;
        const pct = (v) => `${Math.max(0, Math.min(100, (v / maxTotal) * 100)).toFixed(1)}%`;
        return `
          <div class="wf-row">
            <span>Turn #${t.turn_id}</span>
            <div class="wf-bar" title="EOU ${eou}ms | LLM ${llm}ms | Tool ${tool}ms | TTS ${tts}ms">
              <div class="wf-seg sw-eou" style="width:${pct(eou)}"></div>
              <div class="wf-seg sw-llm" style="width:${pct(llm)}"></div>
              <div class="wf-seg sw-tool" style="width:${pct(tool)}"></div>
              <div class="wf-seg sw-tts" style="width:${pct(tts)}"></div>
            </div>
            <span>${Math.round(t.totalMs)} ms</span>
          </div>
        `;
      })
      .join("");
  }

  async function connectLiveKit() {
    if (room) {
      await room.disconnect();
      room = null;
      el.btnConnect.classList.remove("connected");
      el.btnConnect.innerHTML = `<span class="dot"></span> Connect Live Voice`;
      el.btnMute.disabled = true;
      setAgentState("idle");
      return;
    }

    const tokenResp = await fetch("/api/livekit-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        room_name: el.roomName.value.trim() || "pulsecare-clinic-room",
        participant_name: el.patientName.value.trim() || "Patient",
      }),
    });
    const tokenData = await tokenResp.json();
    if (!tokenData.configured) {
      appendTranscript(
        "assistant",
        "LiveKit credentials are not configured in .env yet. Add LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, GROQ_API_KEY, and CARTESIA_API_KEY (or DEEPGRAM_API_KEY), or click 'Simulate Live Call' to test the real FastAPI tool pipeline.",
        true
      );
      return;
    }

    const LiveKit = window.LivekitClient;
    room = new LiveKit.Room({ adaptiveStream: true, dynacast: true });

    room.on(LiveKit.RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === LiveKit.Track.Kind.Audio) {
        const audioEl = track.attach();
        el.remoteAudioContainer.appendChild(audioEl);
      }
    });

    room.on(LiveKit.RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
      if (topic !== TELEMETRY_TOPIC) return;
      try {
        const msg = JSON.parse(new TextDecoder().decode(payload));
        if (msg.type === "agent_state") {
          setAgentState(msg.new_state);
        } else if (msg.type === "transcript") {
          appendTranscript(msg.role, msg.text, msg.is_final);
        } else if (msg.type === "tool_call") {
          appendToolCard(msg);
          refreshSchedule();
        } else if (msg.type === "latency_turn") {
          recordLatencyTurn(msg, "Live WebRTC");
        }
      } catch (err) {
        console.error("Failed to parse telemetry:", err);
      }
    });

    room.on(LiveKit.RoomEvent.Disconnected, () => {
      room = null;
      el.btnConnect.classList.remove("connected");
      el.btnConnect.innerHTML = `<span class="dot"></span> Connect Live Voice`;
      el.btnMute.disabled = true;
      setAgentState("idle");
    });

    await room.connect(tokenData.url, tokenData.token);
    await room.localParticipant.setMicrophoneEnabled(true);
    el.btnConnect.classList.add("connected");
    el.btnConnect.innerHTML = `<span class="dot"></span> Disconnect`;
    el.btnMute.disabled = false;
    isMuted = false;
    el.btnMute.textContent = "Mute Mic";
    setAgentState("listening");
  }

  async function toggleMute() {
    if (!room) return;
    isMuted = !isMuted;
    await room.localParticipant.setMicrophoneEnabled(!isMuted);
    el.btnMute.textContent = isMuted ? "Unmute Mic" : "Mute Mic";
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function runSimulatedLiveCall() {
    el.btnDemo.disabled = true;
    const patient = el.patientName.value.trim() || "Jordan Lee";
    const phone = "555-0184";

    try {
      // Turn 1: Check dental slots tomorrow via REAL FastAPI GET /api/slots
      setAgentState("listening");
      appendTranscript("user", "Hi, do you have any dental appointments available tomorrow?", true);
      await sleep(350);
      setAgentState("thinking");

      const t1 = performance.now();
      const slotsResp = await fetch("/api/slots?specialty=Dentistry&date=tomorrow");
      const slots = await slotsResp.json();
      const tool1Ms = Number((performance.now() - t1).toFixed(1));
      const chosenSlot = slots[0];

      appendToolCard({
        name: "check_available_slots",
        args: { specialty: "Dentistry", date: "tomorrow" },
        duration_ms: tool1Ms,
        success: slotsResp.ok,
        result: { count: slots.length, first_slot: chosenSlot || null },
      });

      setAgentState("speaking");
      const reply1 = chosenSlot
        ? `Hi ${patient}! Dr. Elena Rostova has an opening tomorrow at ${chosenSlot.start_time} in slot ${chosenSlot.id}. Would you like me to book that for you?`
        : "I checked Dentistry for tomorrow, and those slots are currently full. Would you like another day?";
      appendTranscript("assistant", reply1, true);
      recordLatencyTurn(
        {
          turn_id: turnHistory.length + 1,
          eou_ms: 95,
          llm_ttft_ms: 128,
          tokens_per_sec: 265,
          tool_total_ms: tool1Ms,
          tts_ttfb_ms: 108,
          perceived_total_ms: 331 + tool1Ms,
        },
        "Simulated Voice + Real FastAPI"
      );
      await sleep(900);

      if (chosenSlot) {
        // Turn 2: Book the slot via REAL FastAPI POST /api/appointments
        setAgentState("listening");
        appendTranscript(
          "user",
          `Yes please, book it under ${patient}, phone ${phone}, for a dental checkup.`,
          true
        );
        await sleep(350);
        setAgentState("thinking");

        const t2 = performance.now();
        const bookResp = await fetch("/api/appointments", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            slot_id: chosenSlot.id,
            patient_name: patient,
            phone,
            reason: "Dental checkup",
          }),
        });
        const booked = await bookResp.json();
        const tool2Ms = Number((performance.now() - t2).toFixed(1));

        appendToolCard({
          name: "book_appointment",
          args: { slot_id: chosenSlot.id, patient_name: patient, phone, reason: "Dental checkup" },
          duration_ms: tool2Ms,
          success: bookResp.ok,
          result: booked,
        });

        setAgentState("speaking");
        appendTranscript(
          "assistant",
          `You are all set! Your booking ID is ${booked.booking_id} with ${booked.doctor_name} on ${booked.date} at ${booked.start_time}.`,
          true
        );
        recordLatencyTurn(
          {
            turn_id: turnHistory.length + 1,
            eou_ms: 88,
            llm_ttft_ms: 119,
            tokens_per_sec: 282,
            tool_total_ms: tool2Ms,
            tts_ttfb_ms: 102,
            perceived_total_ms: 309 + tool2Ms,
          },
          "Simulated Voice + Real FastAPI"
        );
        await sleep(900);

        // Turn 3: Lookup confirmation via REAL FastAPI GET /api/appointments/lookup
        setAgentState("listening");
        appendTranscript("user", `Can you confirm booking ${booked.booking_id}?`, true);
        await sleep(300);
        setAgentState("thinking");

        const t3 = performance.now();
        const lookupResp = await fetch(
          `/api/appointments/lookup?booking_id=${encodeURIComponent(booked.booking_id)}`
        );
        const found = await lookupResp.json();
        const tool3Ms = Number((performance.now() - t3).toFixed(1));

        appendToolCard({
          name: "lookup_appointment",
          args: { booking_id: booked.booking_id },
          duration_ms: tool3Ms,
          success: lookupResp.ok,
          result: found,
        });

        setAgentState("speaking");
        appendTranscript(
          "assistant",
          `Confirmed! Booking ${booked.booking_id} for ${patient} is active with ${booked.doctor_name} at ${booked.start_time}.`,
          true
        );
        recordLatencyTurn(
          {
            turn_id: turnHistory.length + 1,
            eou_ms: 82,
            llm_ttft_ms: 112,
            tokens_per_sec: 290,
            tool_total_ms: tool3Ms,
            tts_ttfb_ms: 99,
            perceived_total_ms: 293 + tool3Ms,
          },
          "Simulated Voice + Real FastAPI"
        );
      }
    } finally {
      await sleep(400);
      setAgentState("idle");
      el.btnDemo.disabled = false;
      refreshSchedule();
    }
  }

  function connectSse() {
    const sse = new EventSource("/api/events");
    sse.addEventListener("connected", () => {
      el.sseIndicator.textContent = "SSE: live";
    });
    sse.addEventListener("schedule_updated", () => {
      refreshSchedule();
      refreshHealth();
    });
    sse.onerror = () => {
      el.sseIndicator.textContent = "SSE: reconnecting";
    };
  }

  el.btnConnect.addEventListener("click", connectLiveKit);
  el.btnMute.addEventListener("click", toggleMute);
  el.btnDemo.addEventListener("click", runSimulatedLiveCall);
  el.btnReset.addEventListener("click", async () => {
    latestBookingId = null;
    await fetch("/api/reset", { method: "POST" });
    await refreshSchedule();
    await refreshHealth();
  });
  el.btnClearChat.addEventListener("click", () => {
    el.chatFeed.innerHTML = `<div class="empty-state">Conversation log cleared.</div>`;
    turnHistory.length = 0;
    lastAssistantMsgEl = null;
    renderWaterfall();
  });
  el.specialtyFilter.addEventListener("change", refreshSchedule);

  refreshHealth();
  refreshSchedule();
  connectSse();
})();
