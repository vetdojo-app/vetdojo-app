(() => {
  "use strict";

  const root = document.querySelector("[data-voice-app]");
  if (!root) return;

  const ui = {
    start: root.querySelector("[data-voice-start]"),
    end: root.querySelector("[data-voice-end]"),
    mute: root.querySelector("[data-voice-mute]"),
    clear: root.querySelector("[data-voice-clear]"),
    status: root.querySelector("[data-voice-status]"),
    help: root.querySelector("[data-voice-help]"),
    error: root.querySelector("[data-voice-error]"),
    orb: root.querySelector("[data-voice-orb]"),
    audio: root.querySelector("[data-voice-audio]"),
    turns: root.querySelector("[data-voice-turns]"),
    empty: root.querySelector("[data-voice-empty]"),
    sources: root.querySelector("[data-voice-sources]"),
    sourcesEmpty: root.querySelector("[data-voice-sources-empty]"),
    csrf: root.querySelector("[name=csrfmiddlewaretoken]"),
  };

  let peerConnection = null;
  let eventChannel = null;
  let microphoneStream = null;
  let sessionTimer = null;
  let disconnectTimer = null;
  let activeToolRequest = null;
  let muted = false;
  let userSpeaking = false;
  let responseInProgress = false;
  let awaitingResponse = false;
  let toolInProgress = false;
  let isPaused = false;
  let pendingControlCommand = null;
  let connectionAttempt = 0;
  const transcriptNodes = new Map();
  const processedToolCalls = new Set();
  const queryTimeoutMs = Number(root.dataset.queryTimeoutMs) || 120000;

  function setState(state, title, help) {
    root.dataset.state = state;
    ui.status.textContent = title;
    ui.help.textContent = help;
  }

  function showError(message) {
    ui.error.textContent = message;
    ui.error.hidden = false;
    setState("error", "Connection needs attention", "Resolve the issue below, then try again.");
  }

  function clearError() {
    ui.error.hidden = true;
    ui.error.textContent = "";
  }

  function getFriendlyError(error) {
    if (!window.isSecureContext) return "Microphone access requires HTTPS or localhost.";
    if (error && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
      return "Microphone permission was denied. Allow microphone access in your browser and try again.";
    }
    if (error && error.name === "NotFoundError") return "No microphone was found on this device.";
    if (error && error.name === "NotReadableError") return "The microphone is already in use by another application.";
    return error && error.message ? error.message : "The voice connection could not be started.";
  }

  function turnNode(id, role) {
    const key = `${role}:${id || "current"}`;
    if (transcriptNodes.has(key)) return transcriptNodes.get(key);
    if (ui.empty) ui.empty.hidden = true;
    const article = document.createElement("article");
    article.className = `voice-turn voice-turn-${role}`;
    const label = document.createElement("span");
    label.textContent = role === "user" ? "You" : "VetDojo";
    const text = document.createElement("p");
    text.textContent = "…";
    article.append(label, text);
    ui.turns.append(article);
    transcriptNodes.set(key, text);
    article.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return text;
  }

  function appendTranscript(id, role, delta) {
    const node = turnNode(id, role);
    if (node.textContent === "…") node.textContent = "";
    node.textContent += delta || "";
  }

  function completeTranscript(id, role, text) {
    const node = turnNode(id, role);
    if (text) node.textContent = text;
  }

  function sendEvent(event) {
    if (!eventChannel || eventChannel.readyState !== "open") {
      throw new Error("The voice connection is not ready.");
    }
    eventChannel.send(JSON.stringify(event));
  }

  function normalizedControlCommand(transcript) {
    const value = String(transcript || "").toLowerCase().replace(/[^a-z\s]/g, " ").replace(/\s+/g, " ").trim();
    if (["stop", "please stop", "stop please", "stop conversation", "stop the conversation", "end conversation", "end the conversation"].includes(value)) return "stop";
    if (["pause", "please pause", "pause please", "pause conversation", "pause the conversation"].includes(value)) return "pause";
    if (["resume", "please resume", "continue", "please continue", "resume conversation", "resume the conversation", "continue conversation"].includes(value)) return "resume";
    return null;
  }

  function setPausedState() {
    isPaused = true;
    responseInProgress = false;
    awaitingResponse = false;
    setState("paused", "Conversation paused", "Say Resume or Continue when you are ready. Say Stop to end.");
  }

  function applyTranscriptControl(transcript) {
    const command = normalizedControlCommand(transcript);
    if (!command) return false;
    pendingControlCommand = command;
    window.setTimeout(() => {
      if (pendingControlCommand === command) pendingControlCommand = null;
    }, 10000);
    cancelActiveTool();
    if (command === "stop") {
      cleanup();
      setState("ended", "Conversation ended", "Voice command received. Your microphone has been disconnected safely.");
    } else if (command === "pause") {
      setPausedState();
    } else {
      isPaused = false;
      setState("listening", "Listening", "Conversation resumed. Ask your next question.");
    }
    return true;
  }

  function completeControlTool(event, command) {
    try {
      sendEvent({
        type: "conversation.item.create",
        item: {
          type: "function_call_output",
          call_id: event.call_id,
          output: JSON.stringify({ status: command === "stop" ? "ended" : command === "pause" ? "paused" : "resumed" }),
        },
      });
    } catch (_) { /* Transcript fallback may already have closed the session. */ }
    pendingControlCommand = null;
    toolInProgress = false;
    if (command === "stop") {
      cleanup();
      setState("ended", "Conversation ended", "Voice command received. Your microphone has been disconnected safely.");
    } else if (command === "pause") {
      setPausedState();
    } else {
      isPaused = false;
      setState("listening", "Listening", "Conversation resumed. Ask your next question.");
    }
  }

  function cancelActiveTool() {
    if (!activeToolRequest) return;
    activeToolRequest.controller.abort();
    try {
      sendEvent({
        type: "conversation.item.create",
        item: {
          type: "function_call_output",
          call_id: activeToolRequest.callId,
          output: JSON.stringify({ cancelled: true }),
        },
      });
    } catch (_) { /* The connection may already be closing. */ }
    activeToolRequest = null;
    toolInProgress = false;
  }

  function renderSources(result) {
    ui.sources.querySelectorAll(".voice-source-card, .voice-answer-link").forEach((node) => node.remove());
    const sources = Array.isArray(result.sources) ? result.sources : [];
    ui.sourcesEmpty.hidden = sources.length > 0;
    sources.forEach((source) => {
      const card = document.createElement("article");
      card.className = "voice-source-card";
      const heading = document.createElement("strong");
      heading.textContent = `[${source.number}] ${source.title || "VetDojo source"}`;
      const meta = document.createElement("p");
      const page = source.pdf_page ? `PDF page ${source.pdf_page}` : "";
      meta.textContent = [source.type.replaceAll("_", " "), page].filter(Boolean).join(" · ");
      card.append(heading, meta);
      if (source.url) {
        const link = document.createElement("a");
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Open source";
        card.append(link);
      }
      ui.sources.append(card);
    });
    const historyLink = document.createElement("a");
    historyLink.className = "voice-answer-link";
    historyLink.href = result.detail_url;
    historyLink.textContent = sources.length ? "View saved answer and references" : "View saved answer";
    ui.sources.append(historyLink);
  }

  async function runKnowledgeTool(event) {
    if (!event.call_id || processedToolCalls.has(event.call_id)) return;
    processedToolCalls.add(event.call_id);
    const toolCommands = {
      stop_voice_session: "stop",
      pause_voice_session: "pause",
      resume_voice_session: "resume",
    };
    const command = toolCommands[event.name] || (event.name === "search_vetdojo_knowledge" ? pendingControlCommand : null);
    if (command) {
      completeControlTool(event, command);
      return;
    }
    if (event.name !== "search_vetdojo_knowledge") return;
    if (isPaused) {
      try {
        sendEvent({
          type: "conversation.item.create",
          item: { type: "function_call_output", call_id: event.call_id, output: JSON.stringify({ status: "paused" }) },
        });
      } catch (_) { /* Connection errors are handled separately. */ }
      setPausedState();
      return;
    }
    toolInProgress = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), queryTimeoutMs);
    activeToolRequest = { callId: event.call_id, controller };
    setState("processing", "Searching VetDojo", "Checking approved knowledge and preparing the answer...");
    let output;
    try {
      const args = JSON.parse(event.arguments || "{}");
      const response = await fetch(root.dataset.queryUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": ui.csrf.value,
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ question: args.question, call_id: event.call_id }),
        signal: controller.signal,
      });
      output = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(output.error || "VetDojo could not answer that question.");
      renderSources(output);
    } catch (error) {
      window.clearTimeout(timeout);
      if (error && error.name === "AbortError") {
        if (activeToolRequest && activeToolRequest.callId === event.call_id) {
          activeToolRequest = null;
          toolInProgress = false;
          try {
            sendEvent({
              type: "conversation.item.create",
              item: {
                type: "function_call_output",
                call_id: event.call_id,
                output: JSON.stringify({ cancelled: true }),
              },
            });
          } catch (_) { /* The session may have ended. */ }
          showError("The knowledge search took too long. Please ask the question again.");
        }
        return;
      }
      output = { error: getFriendlyError(error) };
      ui.error.textContent = output.error;
      ui.error.hidden = false;
    }
    window.clearTimeout(timeout);
    if (!activeToolRequest || activeToolRequest.callId !== event.call_id) return;
    activeToolRequest = null;
    try {
      sendEvent({
        type: "conversation.item.create",
        item: { type: "function_call_output", call_id: event.call_id, output: JSON.stringify(output) },
      });
      toolInProgress = false;
      sendEvent({
        type: "response.create",
        response: {
          tool_choice: "none",
          instructions: "Respond in English only. Speak only the tool's spoken_answer. Keep citation markers and references on screen; do not read them aloud. If the tool returned an error, briefly ask the user to try again in English.",
        },
      });
    } catch (error) {
      toolInProgress = false;
      showError(getFriendlyError(error));
    }
  }

  function handleRealtimeEvent(event) {
    switch (event.type) {
      case "session.created":
      case "session.updated":
        if (!isPaused) setState("listening", "Listening", "Ask a short question to test the voice connection.");
        break;
      case "input_audio_buffer.speech_started":
        cancelActiveTool();
        userSpeaking = true;
        awaitingResponse = false;
        setState(
          "hearing",
          responseInProgress ? "Response interrupted - listening" : "I can hear you",
          "Keep speaking naturally. VetDojo will wait for a short pause."
        );
        break;
      case "input_audio_buffer.speech_stopped":
        userSpeaking = false;
        awaitingResponse = true;
        setState("processing", "Processing speech", "Preparing the test response…");
        break;
      case "conversation.item.input_audio_transcription.delta":
        appendTranscript(event.item_id, "user", event.delta);
        break;
      case "conversation.item.input_audio_transcription.completed":
        completeTranscript(event.item_id, "user", event.transcript);
        applyTranscriptControl(event.transcript);
        break;
      case "response.created":
        if (isPaused) {
          setPausedState();
          break;
        }
        awaitingResponse = false;
        responseInProgress = true;
        setState("processing", "Preparing response", "VetDojo is about to speak.");
        break;
      case "response.function_call_arguments.done":
        responseInProgress = false;
        runKnowledgeTool(event);
        break;
      case "response.output_audio_transcript.delta":
        if (isPaused) break;
        responseInProgress = true;
        setState("speaking", "VetDojo is speaking", "You can listen or start speaking to interrupt.");
        appendTranscript(event.item_id || event.response_id, "assistant", event.delta);
        break;
      case "response.output_audio_transcript.done":
        completeTranscript(event.item_id || event.response_id, "assistant", event.transcript);
        break;
      case "response.done":
        responseInProgress = false;
        if (toolInProgress) break;
        if (isPaused) {
          setPausedState();
          break;
        }
        if (event.response && ["failed", "incomplete"].includes(event.response.status)) {
          showError("The spoken response could not be completed. Please ask the question again.");
          break;
        }
        if (userSpeaking) {
          setState("hearing", "Listening to your interruption", "Keep speaking naturally.");
        } else if (awaitingResponse) {
          setState("processing", "Processing speech", "Preparing the next response.");
        } else {
          setState("listening", "Listening", "Ask another question or end the conversation.");
        }
        break;
      case "output_audio_buffer.cleared":
        responseInProgress = false;
        if (userSpeaking) {
          setState("hearing", "Response interrupted - listening", "Keep speaking naturally.");
        }
        break;
      case "conversation.item.input_audio_transcription.failed":
        ui.error.textContent = "Your speech was heard, but its written transcript could not be created. You can continue speaking.";
        ui.error.hidden = false;
        break;
      case "error":
        showError(event.error && event.error.message ? event.error.message : "The realtime service reported an error.");
        break;
      default:
        break;
    }
  }

  function cleanup({ preserveStatus = false } = {}) {
    connectionAttempt += 1;
    cancelActiveTool();
    if (sessionTimer) window.clearTimeout(sessionTimer);
    if (disconnectTimer) window.clearTimeout(disconnectTimer);
    sessionTimer = null;
    disconnectTimer = null;
    if (eventChannel) eventChannel.close();
    eventChannel = null;
    if (peerConnection) peerConnection.close();
    peerConnection = null;
    if (microphoneStream) microphoneStream.getTracks().forEach((track) => track.stop());
    microphoneStream = null;
    ui.audio.srcObject = null;
    muted = false;
    userSpeaking = false;
    responseInProgress = false;
    awaitingResponse = false;
    toolInProgress = false;
    isPaused = false;
    pendingControlCommand = null;
    processedToolCalls.clear();
    ui.mute.textContent = "Mute microphone";
    ui.mute.setAttribute("aria-pressed", "false");
    ui.start.disabled = false;
    ui.end.disabled = true;
    ui.mute.disabled = true;
    if (!preserveStatus) setState("idle", "Ready to start", "Select Start conversation to reconnect.");
  }

  async function requestClientSecret() {
    const response = await fetch(root.dataset.sessionUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": ui.csrf.value, Accept: "application/json" },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "VetDojo could not create a voice session.");
    return data;
  }

  async function startConversation() {
    clearError();
    if (!window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showError("This browser requires HTTPS and microphone support for voice conversations.");
      return;
    }

    const attempt = ++connectionAttempt;
    const requireCurrentAttempt = () => {
      if (attempt !== connectionAttempt) throw new DOMException("Connection cancelled", "AbortError");
    };
    ui.start.disabled = true;
    ui.end.disabled = false;
    setState("connecting", "Requesting microphone", "Allow microphone access when your browser asks.");

    try {
      microphoneStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      requireCurrentAttempt();
      setState("connecting", "Starting secure session", "Connecting to the realtime voice service…");
      const session = await requestClientSecret();
      requireCurrentAttempt();

      peerConnection = new RTCPeerConnection();
      const connection = peerConnection;
      connection.ontrack = ({ streams }) => {
        ui.audio.srcObject = streams[0];
        ui.audio.play().catch(() => {
          showError("Your browser blocked audio playback. Select Start conversation again.");
        });
      };
      connection.onconnectionstatechange = () => {
        if (disconnectTimer) window.clearTimeout(disconnectTimer);
        disconnectTimer = null;
        if (["failed", "closed"].includes(connection.connectionState)) {
          showError("The realtime connection was interrupted. Please start a new conversation.");
          cleanup({ preserveStatus: true });
        } else if (connection.connectionState === "disconnected") {
          setState("connecting", "Connection interrupted", "Trying to restore the voice connection...");
          disconnectTimer = window.setTimeout(() => {
            if (connection.connectionState === "disconnected") {
              showError("The realtime connection was interrupted. Please start a new conversation.");
              cleanup({ preserveStatus: true });
            }
          }, 5000);
        } else if (connection.connectionState === "connected") {
          setState("listening", "Listening", "Ask another question or continue the conversation.");
        }
      };
      microphoneStream.getTracks().forEach((track) => connection.addTrack(track, microphoneStream));

      eventChannel = connection.createDataChannel("oai-events");
      eventChannel.addEventListener("open", () => {
        ui.end.disabled = false;
        ui.mute.disabled = false;
        setState("listening", "Listening", "Ask a short question to test the voice connection.");
      });
      eventChannel.addEventListener("message", ({ data }) => {
        try { handleRealtimeEvent(JSON.parse(data)); } catch (_) { /* Ignore malformed external events. */ }
      });
      eventChannel.addEventListener("error", () => {
        showError("The realtime event channel reported an error. Please start a new conversation.");
      });

      const offer = await connection.createOffer();
      await connection.setLocalDescription(offer);
      requireCurrentAttempt();
      const realtimeResponse = await fetch("https://api.openai.com/v1/realtime/calls", {
        method: "POST",
        body: offer.sdp,
        headers: { Authorization: `Bearer ${session.client_secret}`, "Content-Type": "application/sdp" },
      });
      if (!realtimeResponse.ok) throw new Error("The realtime voice service rejected the connection.");
      requireCurrentAttempt();
      await connection.setRemoteDescription({ type: "answer", sdp: await realtimeResponse.text() });

      sessionTimer = window.setTimeout(() => {
        setState("ended", "Session time reached", "Start a new conversation if you would like to continue.");
        cleanup({ preserveStatus: true });
      }, session.max_session_minutes * 60 * 1000);
    } catch (error) {
      if (error && error.name === "AbortError") return;
      showError(getFriendlyError(error));
      cleanup({ preserveStatus: true });
    }
  }

  function toggleMute() {
    if (!microphoneStream) return;
    muted = !muted;
    microphoneStream.getAudioTracks().forEach((track) => { track.enabled = !muted; });
    ui.mute.textContent = muted ? "Unmute microphone" : "Mute microphone";
    ui.mute.setAttribute("aria-pressed", String(muted));
    setState(muted ? "muted" : "listening", muted ? "Microphone muted" : "Listening", muted ? "Unmute when you are ready to continue." : "Ask another question or end the conversation.");
  }

  ui.start.addEventListener("click", startConversation);
  ui.end.addEventListener("click", () => {
    cleanup();
    setState("ended", "Conversation ended", "Your microphone has been disconnected safely.");
  });
  ui.mute.addEventListener("click", toggleMute);
  ui.clear.addEventListener("click", () => {
    transcriptNodes.clear();
    ui.turns.querySelectorAll(".voice-turn").forEach((node) => node.remove());
    if (ui.empty) ui.empty.hidden = false;
  });
  window.addEventListener("pagehide", () => cleanup({ preserveStatus: true }));
})();
