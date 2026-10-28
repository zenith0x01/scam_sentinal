(() => {
  const $ = (id) => document.getElementById(id);

  const screens = { lobby: $("lobby"), call: $("call-screen"), summary: $("summary-screen") };
  function showScreen(name) {
    Object.values(screens).forEach((s) => s.classList.add("hidden"));
    screens[name].classList.remove("hidden");
  }

  const ICE_SERVERS = [{ urls: "stun:stun.l.google.com:19302" }];
  // Longer chunks give Groq Whisper enough sentence context.
  const CHUNK_MS = 8000;

  const state = {
    username: "",
    roomId: "",
    peerUsername: null,
    ws: null,
    pc: null,
    localStream: null,
    muted: false,
    demo: false,
    callStart: null,
    durationTimer: null,
    stopChunking: false,
    lastSummary: null,
  };

  function randomRoomCode() {
    const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    let out = "";
    for (let i = 0; i < 6; i++) out += alphabet[Math.floor(Math.random() * alphabet.length)];
    return out;
  }

  function setLobbyNote(msg) { $("lobby-note").textContent = msg || ""; }

  // ---------------- Lobby wiring ----------------

  $("create-room-btn").addEventListener("click", () => {
    const username = $("username-input").value.trim();
    if (!username) return setLobbyNote("Enter your name first.");
    enterCall(randomRoomCode(), username, { demo: false });
  });

  $("join-room-btn").addEventListener("click", () => {
    const username = $("username-input").value.trim();
    const room = $("room-input").value.trim().toUpperCase();
    if (!username) return setLobbyNote("Enter your name first.");
    if (!room) return setLobbyNote("Enter a room code to join.");
    enterCall(room, username, { demo: false });
  });

  $("demo-scam-btn").addEventListener("click", () => {
    const username = $("username-input").value.trim() || "You";
    enterCall("DEMO-" + randomRoomCode(), username, { demo: true, scenario: "scam" });
  });

  $("demo-normal-btn").addEventListener("click", () => {
    const username = $("username-input").value.trim() || "You";
    enterCall("DEMO-" + randomRoomCode(), username, { demo: true, scenario: "normal" });
  });

  $("new-call-btn").addEventListener("click", () => {
    showScreen("lobby");
    setLobbyNote("");
  });

  // ---------------- Call setup ----------------

  async function enterCall(roomId, username, opts) {
    state.roomId = roomId;
    state.username = username;
    state.demo = !!opts.demo;
    state.stopChunking = false;

    $("room-label").textContent = roomId;
    $("name-self").textContent = username + " (you)";
    $("avatar-self").textContent = username[0]?.toUpperCase() || "?";
    resetCallUI();
    showScreen("call");

    if (!state.demo) {
      if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setLobbyNote("");
        setConnText("Blocked: this page isn't a secure context, so the browser won't allow microphone access.");
        showMicHelp();
        return;
      }
      try {
      // Preserve played recordings for server-side analysis. Browser echo
      // cancellation/noise suppression can otherwise remove the MP3 voice.
      state.localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
          channelCount: 1,
        },
      });
      } catch (err) {
        console.error("getUserMedia failed:", err);
        setConnText("Microphone access denied (" + err.name + ") — call cannot start.");
        showMicHelp();
        return;
      }
    }

    connectWebSocket(opts.scenario);
  }

  function resetCallUI() {
    $("name-peer").textContent = "Waiting for peer…";
    $("avatar-peer").textContent = "?";
    $("mic-status-self").textContent = state.demo ? "Demo mode (no mic)" : "Mic live";
    $("mic-status-peer").textContent = "—";
    $("scam-risk-level").textContent = "LOW";
    $("scam-risk-level").className = "risk-value";
    $("scam-risk-bar").style.width = "0%";
    $("scam-risk-bar").style.background = "";
    $("scam-risk-confidence").textContent = "0% confidence";
    $("scam-risk-backend").textContent = "Local heuristic";
    $("voice-risk-level").textContent = "—";
    $("voice-risk-level").className = "risk-value";
    $("voice-risk-bar").style.width = "0%";
    $("voice-risk-confidence").textContent = state.demo
      ? "Demo mode analyzes transcript text only — no live audio sample."
      : "Listening for known-script evidence…";
    $("reasons-list").innerHTML = '<li class="reasons-empty">No suspicious signals detected yet.</li>';
    $("alert-banner").classList.add("hidden");
    $("transcript-feed").innerHTML = "";
    $("mute-btn").classList.remove("muted");
    $("mute-btn").textContent = "🎤";
    $("leave-btn").style.display = "";
    state.chunksSent = 0;
    state.linesReceived = 0;
    updateDiagnostics(state.demo ? "" : "Chunks sent: 0 | Transcribed lines: 0");
    if (state.demo) {
      $("mute-btn").style.display = "none";
      $("card-peer").style.display = "none";
    } else {
      $("mute-btn").style.display = "";
      $("card-peer").style.display = "";
    }
    state.callStart = Date.now();
    clearInterval(state.durationTimer);
    state.durationTimer = setInterval(updateDuration, 1000);
    updateDuration();
  }

  function updateDuration() {
    const secs = Math.floor((Date.now() - state.callStart) / 1000);
    const m = String(Math.floor(secs / 60)).padStart(2, "0");
    const s = String(secs % 60).padStart(2, "0");
    $("call-duration").textContent = `${m}:${s}`;
  }

  function updateDiagnostics(text) {
    const el = $("diagnostics-line");
    if (el) el.textContent = text;
  }

  function showMicHelp() {
    const el = $("analysis-indicator");
    el.innerHTML =
      '⚠ Microphone requires HTTPS (or http://localhost). If you\'re on another laptop, ' +
      "connect with <code>https://&lt;host-ip&gt;:8000</code> instead of <code>http://</code> — see README section " +
      '"Two laptops on a LAN".';
    el.style.color = "var(--high)";
  }

  function setConnText(text, statusClass) {
    $("conn-text").textContent = text;
    const dot = $("conn-dot");
    dot.className = "status-dot" + (statusClass ? " " + statusClass : "");
  }

  // ---------------- WebSocket signaling ----------------

  function connectWebSocket(demoScenario) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/${encodeURIComponent(state.roomId)}/${encodeURIComponent(state.username)}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    state.ws = ws;

    setConnText("Connecting…");

    ws.onopen = () => {
      if (state.demo) {
        setConnText("Demo running", "connected");
      }
    };

    ws.onclose = () => setConnText("Disconnected", "disconnected");
    ws.onerror = () => setConnText("Connection error", "disconnected");

    ws.onmessage = async (event) => {
      const data = JSON.parse(event.data);
      switch (data.type) {
        case "room-full":
          setConnText("Room is full (2 max)", "disconnected");
          break;

        case "joined":
          if (state.demo) {
            ws.send(JSON.stringify({ type: "start-demo", scenario: demoScenario }));
          } else {
            setConnText(data.peer ? "Connected" : "Waiting for someone to join…", data.peer ? "connected" : "");
            if (data.peer) setPeerName(data.peer);
            startChunkRecorder();
            if (data.should_initiate) {
              await ensurePeerConnection();
              await createAndSendOffer();
            }
          }
          break;

        case "peer-joined":
          setPeerName(data.peer);
          setConnText("Connected", "connected");
          break;

        case "peer-left":
          if (data.peer === state.peerUsername) {
            $("name-peer").textContent = "Peer left";
            $("mic-status-peer").textContent = "—";
            setConnText("Peer disconnected", "disconnected");
          }
          break;

        case "participants-update":
          break;

        case "webrtc-offer":
          await ensurePeerConnection();
          await state.pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
          const answer = await state.pc.createAnswer();
          await state.pc.setLocalDescription(answer);
          ws.send(JSON.stringify({ type: "webrtc-answer", target: data.from, sdp: answer }));
          break;

        case "webrtc-answer":
          if (state.pc) await state.pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
          break;

        case "webrtc-ice":
          if (state.pc && data.candidate) {
            try { await state.pc.addIceCandidate(data.candidate); } catch (e) {}
          }
          break;

        case "peer-mute-status":
          $("mic-status-peer").textContent = data.muted ? "Muted" : "Mic live";
          break;

        case "transcript-update":
          appendTranscriptLine(data.entry.speaker, data.entry.text);
          state.linesReceived = (state.linesReceived || 0) + 1;
          updateDiagnostics(`Chunks sent: ${state.chunksSent || 0} | Transcribed lines: ${state.linesReceived}`);
          break;

        case "risk-update":
          updateScamRisk(data);
          break;

        case "voice-update":
          updateVoiceRisk(data.result, data.speaker);
          break;

        case "call-ended":
          finishCall(data.summary);
          break;
      }
    };
  }

  function setPeerName(name) {
    state.peerUsername = name;
    $("name-peer").textContent = name;
    $("avatar-peer").textContent = name[0]?.toUpperCase() || "?";
    $("mic-status-peer").textContent = "Mic live";
  }

  // ---------------- WebRTC peer connection ----------------

  async function ensurePeerConnection() {
    if (state.pc) return state.pc;
    const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
    state.pc = pc;

    if (state.localStream) {
      state.localStream.getTracks().forEach((track) => pc.addTrack(track, state.localStream));
    }

    pc.ontrack = (event) => {
      const audioEl = $("remote-audio");
      audioEl.srcObject = event.streams[0];
    };

    pc.onicecandidate = (event) => {
      if (event.candidate && state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({
          type: "webrtc-ice",
          target: state.peerUsername,
          candidate: event.candidate,
        }));
      }
    };

    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "connected") setConnText("Connected", "connected");
      else if (["disconnected", "failed", "closed"].includes(pc.connectionState)) {
        setConnText("Connection " + pc.connectionState, "disconnected");
      }
    };

    return pc;
  }

  async function createAndSendOffer() {
    const pc = state.pc;
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    state.ws.send(JSON.stringify({ type: "webrtc-offer", target: state.peerUsername, sdp: offer }));
  }

  // ---------------- Chunked audio capture for analysis ----------------

  const CANDIDATE_MIME_TYPES = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/ogg",
    "audio/mp4",
  ];

  function pickSupportedMimeType() {
    if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return "";
    for (const t of CANDIDATE_MIME_TYPES) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return ""; // let the browser pick its own default
  }

  function startChunkRecorder() {
    if (!state.localStream) return;
    const supportedType = pickSupportedMimeType();
    console.log("[ScamSentinel] recording chunks with mimeType:", supportedType || "(browser default)");
    state.chunksSent = 0;

    const cycle = () => {
      if (state.stopChunking || !state.localStream) return;
      let recorder;
      try {
        recorder = supportedType
          ? new MediaRecorder(state.localStream, { mimeType: supportedType })
          : new MediaRecorder(state.localStream);
      } catch (e) {
        console.error("[ScamSentinel] MediaRecorder failed to start:", e);
        updateDiagnostics("Recorder error: " + e.message);
        return;
      }
      const chunks = [];
      recorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunks.push(e.data); };
      recorder.onerror = (e) => console.error("[ScamSentinel] recorder error:", e.error || e);
      recorder.onstop = async () => {
        if (chunks.length && state.ws && state.ws.readyState === WebSocket.OPEN && !state.muted) {
          const blob = new Blob(chunks, { type: recorder.mimeType || supportedType || "audio/webm" });
          const buf = await blob.arrayBuffer();
          if (state.ws.readyState === WebSocket.OPEN) {
            state.ws.send(buf);
            state.chunksSent = (state.chunksSent || 0) + 1;
            console.log(`[ScamSentinel] sent audio chunk #${state.chunksSent} (${buf.byteLength} bytes)`);
            updateDiagnostics(`Chunks sent: ${state.chunksSent} | Transcribed lines: ${state.linesReceived || 0}`);
          }
        } else if (chunks.length === 0) {
          console.warn("[ScamSentinel] recorder produced an empty chunk (no audio captured this cycle)");
        }
        if (!state.stopChunking) setTimeout(cycle, 50);
      };
      recorder.start();
      setTimeout(() => { try { recorder.stop(); } catch (e) {} }, CHUNK_MS);
    };
    cycle();
  }

  // ---------------- Mute / Leave ----------------

  $("mute-btn").addEventListener("click", () => {
    if (!state.localStream) return;
    state.muted = !state.muted;
    state.localStream.getAudioTracks().forEach((t) => (t.enabled = !state.muted));
    $("mute-btn").classList.toggle("muted", state.muted);
    $("mute-btn").textContent = state.muted ? "🔇" : "🎤";
    $("mic-status-self").textContent = state.muted ? "Muted" : "Mic live";
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: "mute-status", muted: state.muted }));
    }
  });

  $("leave-btn").addEventListener("click", () => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: "end-call" }));
    }
    teardownCall();
    // If server doesn't answer promptly, still show a basic summary from client state.
    setTimeout(() => {
      if (screens.call.classList.contains("hidden")) return;
      finishCall(state.lastSummary || { duration_seconds: Math.floor((Date.now() - state.callStart) / 1000), max_risk_level: "LOW", max_risk_confidence: 0, suspicious_events: [], transcript: [] });
    }, 600);
  });

  function teardownCall() {
    state.stopChunking = true;
    clearInterval(state.durationTimer);
    if (state.pc) { state.pc.close(); state.pc = null; }
    if (state.localStream) { state.localStream.getTracks().forEach((t) => t.stop()); state.localStream = null; }
  }

  function finishCall(summary) {
    if (screens.call.classList.contains("hidden") && screens.summary.classList.contains("hidden") === false) return;
    teardownCall();
    if (state.ws) { try { state.ws.close(); } catch (e) {} state.ws = null; }
    state.lastSummary = summary;
    renderSummary(summary);
    showScreen("summary");
  }

  function renderSummary(summary) {
    const mins = String(Math.floor(summary.duration_seconds / 60)).padStart(2, "0");
    const secs = String(summary.duration_seconds % 60).padStart(2, "0");
    $("sum-duration").textContent = `${mins}:${secs}`;
    $("sum-risk").textContent = summary.max_risk_level;
    $("sum-risk").style.color = { LOW: "var(--low)", MEDIUM: "var(--medium)", HIGH: "var(--high)" }[summary.max_risk_level] || "";
    $("sum-confidence").textContent = summary.max_risk_confidence + "%";

    const eventsEl = $("sum-events");
    if (summary.suspicious_events && summary.suspicious_events.length) {
      eventsEl.innerHTML = summary.suspicious_events.map((e) => `<li>${escapeHtml(e)}</li>`).join("");
    } else {
      eventsEl.innerHTML = '<li class="reasons-empty">None detected.</li>';
    }

    const tEl = $("sum-transcript");
    tEl.innerHTML = "";
    (summary.transcript || []).forEach((entry) => {
      const line = document.createElement("div");
      line.className = "transcript-line";
      line.innerHTML = `<span class="transcript-speaker">${escapeHtml(entry.speaker)}:</span>${escapeHtml(entry.text)}`;
      tEl.appendChild(line);
    });
  }

  // ---------------- UI update helpers ----------------

  function appendTranscriptLine(speaker, text) {
    const feed = $("transcript-feed");
    const line = document.createElement("div");
    line.className = "transcript-line";
    line.innerHTML = `<span class="transcript-speaker">${escapeHtml(speaker)}:</span>${escapeHtml(text)}`;
    feed.appendChild(line);
    feed.scrollTop = feed.scrollHeight;
  }

  function updateScamRisk(data) {
    const levelEl = $("scam-risk-level");
    levelEl.textContent = data.risk_level;
    levelEl.className = "risk-value " + data.risk_level;
    $("scam-risk-bar").style.width = data.confidence + "%";
    $("scam-risk-bar").style.background = { LOW: "var(--low)", MEDIUM: "var(--medium)", HIGH: "var(--high)" }[data.risk_level];
    $("scam-risk-confidence").textContent = data.confidence + "% confidence";
    $("scam-risk-backend").textContent = data.backend && data.backend.startsWith("gemini:")
      ? "Gemini analysis" : "Local heuristic";

    const reasonsEl = $("reasons-list");
    if (data.reasons && data.reasons.length) {
      reasonsEl.innerHTML = data.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join("");
    } else {
      reasonsEl.innerHTML = '<li class="reasons-empty">No suspicious signals detected yet.</li>';
    }

    const banner = $("alert-banner");
    if (data.risk_level === "HIGH") banner.classList.remove("hidden");
    else banner.classList.add("hidden");
  }

  function updateVoiceRisk(result, speakerLabel) {
    const levelEl = $("voice-risk-level");
    const pct = result.ai_probability;
    if (pct === null || pct === undefined) {
      levelEl.textContent = result.prediction === "ERROR" ? "Error" : "—";
      levelEl.className = "risk-value";
      $("voice-risk-bar").style.width = "0%";
      $("voice-risk-confidence").textContent = `${result.label || "Voice analysis unavailable"} (${speakerLabel})${result.error ? ": " + result.error : ""}`;
      return;
    }
    let cls = "";
    if (pct >= 65) cls = "HIGH"; else if (pct >= 40) cls = "MEDIUM"; else cls = "";
    levelEl.textContent = `${result.prediction || (pct >= 50 ? "AI" : "HUMAN")} ${Math.round(result.confidence ?? Math.max(pct, 100 - pct))}%`;
    levelEl.className = "risk-value " + cls;
    $("voice-risk-bar").style.width = pct + "%";
    $("voice-risk-bar").style.background = cls === "HIGH" ? "var(--high)" : cls === "MEDIUM" ? "var(--medium)" : "var(--accent)";
    $("voice-risk-confidence").textContent = `${result.label} · analyzed user: ${speakerLabel}`;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }
})();
