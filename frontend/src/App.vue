<script setup>
import { computed, onMounted, reactive, ref } from "vue";

const apiBase = import.meta.env.VITE_API_BASE || "/api";
const wsBase = import.meta.env.VITE_WS_BASE || `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

const status = reactive({
  api: "Checking",
  apiDetail: "",
  loading: false,
  error: "",
});

const encounters = ref([]);
const selectedEncounter = ref(null);
const selectedId = ref("");
const recommendForm = reactive({
  setting: "office outpatient",
  patient_type: "established patient",
  service_date: new Date().toISOString().slice(0, 10),
  total_time_minutes: "",
  mdm_problems: "",
  mdm_data: "",
  mdm_risk: "",
  same_day_separate_em: false,
});

const recorderState = reactive({
  sessionId: `session-${Date.now()}`,
  connected: false,
  recording: false,
  message: "Idle",
  encounterId: "",
  transcript: "",
  events: [],
});

let socket = null;
let mediaRecorder = null;
let mediaStream = null;

const selectedSummary = computed(() => {
  if (!selectedEncounter.value) {
    return "No encounter selected";
  }
  return `${selectedEncounter.value.status} / ${selectedEncounter.value.encounter_id}`;
});

const recommendationPayload = computed(() => {
  const facts = {};
  if (recommendForm.total_time_minutes !== "") {
    facts.total_time_minutes = Number(recommendForm.total_time_minutes);
  }
  for (const key of ["mdm_problems", "mdm_data", "mdm_risk"]) {
    if (recommendForm[key].trim()) {
      facts[key] = recommendForm[key].trim();
    }
  }
  if (recommendForm.same_day_separate_em) {
    facts.same_day_separate_em = true;
  }
  return Object.keys(facts).length ? facts : null;
});

async function apiFetch(path, options = {}) {
  status.error = "";
  const response = await fetch(`${apiBase}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the HTTP status text.
    }
    throw new Error(detail);
  }
  return response.json();
}

async function checkApi() {
  try {
    const payload = await apiFetch("/encounters?limit=1");
    status.api = "Online";
    status.apiDetail = `${payload.items.length} recent encounter${payload.items.length === 1 ? "" : "s"} reachable`;
  } catch (error) {
    status.api = "Offline";
    status.apiDetail = error.message;
  }
}

async function loadEncounters() {
  status.loading = true;
  try {
    const payload = await apiFetch("/encounters?limit=50");
    encounters.value = payload.items;
    if (!selectedId.value && payload.items.length) {
      await selectEncounter(payload.items[0].encounter_id);
    }
  } catch (error) {
    status.error = error.message;
  } finally {
    status.loading = false;
  }
}

async function selectEncounter(encounterId) {
  selectedId.value = encounterId;
  status.loading = true;
  try {
    selectedEncounter.value = await apiFetch(`/encounters/${encounterId}`);
  } catch (error) {
    status.error = error.message;
  } finally {
    status.loading = false;
  }
}

async function createSoapNote() {
  if (!selectedId.value) {
    status.error = "Select an encounter first.";
    return;
  }
  status.loading = true;
  try {
    await apiFetch("/note", {
      method: "POST",
      body: JSON.stringify({ encounter_id: selectedId.value }),
    });
    await selectEncounter(selectedId.value);
    await loadEncounters();
  } catch (error) {
    status.error = error.message;
  } finally {
    status.loading = false;
  }
}

async function createRecommendation() {
  if (!selectedId.value) {
    status.error = "Select an encounter first.";
    return;
  }
  status.loading = true;
  try {
    await apiFetch("/recommend", {
      method: "POST",
      body: JSON.stringify({
        encounter_id: selectedId.value,
        setting: recommendForm.setting,
        patient_type: recommendForm.patient_type,
        service_date: recommendForm.service_date,
        documentation_facts: recommendationPayload.value,
      }),
    });
    await selectEncounter(selectedId.value);
    await loadEncounters();
  } catch (error) {
    status.error = error.message;
  } finally {
    status.loading = false;
  }
}

function addRecorderEvent(message) {
  recorderState.events.unshift({
    id: `${Date.now()}-${Math.random()}`,
    at: new Date().toLocaleTimeString(),
    message,
  });
  recorderState.events = recorderState.events.slice(0, 12);
}

async function startRecording() {
  if (recorderState.recording) {
    return;
  }
  recorderState.message = "Requesting microphone";
  recorderState.transcript = "";
  recorderState.encounterId = "";
  recorderState.events = [];

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    socket = new WebSocket(`${wsBase}/audio/${encodeURIComponent(recorderState.sessionId)}`);

    socket.addEventListener("open", () => {
      recorderState.connected = true;
      recorderState.message = "Connected";
      addRecorderEvent("Websocket connected");
    });

    socket.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      addRecorderEvent(payload.type);
      if (payload.encounter_id) {
        recorderState.encounterId = payload.encounter_id;
      }
      if (payload.text) {
        recorderState.transcript = payload.text;
      }
      if (payload.message) {
        recorderState.message = payload.message;
      }
      if (payload.type === "transcript.final") {
        recorderState.message = "Transcript stored";
        loadEncounters();
      }
    });

    socket.addEventListener("close", () => {
      recorderState.connected = false;
      recorderState.recording = false;
      recorderState.message = "Disconnected";
      addRecorderEvent("Websocket closed");
      cleanupMedia();
    });

    socket.addEventListener("error", () => {
      recorderState.message = "Websocket error";
      addRecorderEvent("Websocket error");
    });

    mediaRecorder = new MediaRecorder(mediaStream, { mimeType: "audio/webm" });
    mediaRecorder.addEventListener("dataavailable", async (event) => {
      if (event.data.size > 0 && socket?.readyState === WebSocket.OPEN) {
        socket.send(await event.data.arrayBuffer());
      }
    });
    mediaRecorder.start(3000);
    recorderState.recording = true;
    recorderState.message = "Recording";
  } catch (error) {
    recorderState.message = error.message;
    cleanupMedia();
  }
}

function stopRecording() {
  if (mediaRecorder?.state === "recording") {
    mediaRecorder.stop();
  }
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send("stop");
  }
  recorderState.recording = false;
  recorderState.message = "Stopping";
  cleanupMedia();
}

function cleanupMedia() {
  mediaStream?.getTracks().forEach((track) => track.stop());
  mediaStream = null;
  mediaRecorder = null;
}

onMounted(async () => {
  await checkApi();
  await loadEncounters();
});
</script>

<template>
  <main class="page-shell">
    <div class="demo-reference" aria-hidden="true"></div>
    <section class="app-frame">
      <nav class="rail" aria-label="Primary">
        <div class="rail-logo">PA</div>
        <button class="rail-button active" type="button" title="Overview">⌂</button>
        <button class="rail-button" type="button" title="Encounters">≡</button>
        <button class="rail-button" type="button" title="Reports">▧</button>
        <button class="rail-button" type="button" title="Settings">⚙</button>
      </nav>

    <aside class="sidebar">
      <div class="brand-block">
        <div class="brand-mark">PA</div>
        <div>
          <h1>Clinical Overview</h1>
          <p>Encounter documentation and coding support</p>
        </div>
      </div>

      <section class="panel compact">
        <div class="panel-header">
          <h2>API</h2>
          <span :class="['pill', status.api.toLowerCase()]">{{ status.api }}</span>
        </div>
        <p class="muted">{{ status.apiDetail }}</p>
        <button class="secondary" type="button" @click="checkApi">Check</button>
      </section>

      <section class="panel encounters-panel">
        <div class="panel-header">
          <h2>Encounters</h2>
          <button class="icon-button" type="button" title="Refresh encounters" @click="loadEncounters">
            ↻
          </button>
        </div>
        <div class="encounter-list">
          <button
            v-for="encounter in encounters"
            :key="encounter.encounter_id"
            :class="['encounter-row', { active: selectedId === encounter.encounter_id }]"
            type="button"
            @click="selectEncounter(encounter.encounter_id)"
          >
            <span>{{ encounter.status }}</span>
            <small>{{ encounter.encounter_id }}</small>
          </button>
          <p v-if="!encounters.length" class="empty">No encounters yet</p>
        </div>
      </section>
    </aside>

    <section class="workspace">
      <header class="topbar">
        <div>
          <p class="eyebrow">Current Encounter</p>
          <h2>{{ selectedSummary }}</h2>
        </div>
        <label class="search-box">
          <span>⌕</span>
          <input placeholder="Search encounters" />
        </label>
        <div class="topbar-actions">
          <span :class="['pill', status.api.toLowerCase()]">{{ status.api }}</span>
          <button class="secondary" type="button" :disabled="status.loading" @click="loadEncounters">
            Refresh
          </button>
        </div>
      </header>

      <p v-if="status.error" class="error-banner">{{ status.error }}</p>

      <div class="grid">
        <section class="panel recorder">
          <div class="panel-header">
            <h2>Audio Capture</h2>
            <span :class="['pill', recorderState.recording ? 'online' : 'neutral']">
              {{ recorderState.message }}
            </span>
          </div>
          <div class="metric-card hero-metric">
            <span>Live transcription</span>
            <strong>{{ recorderState.recording ? "Active" : "Ready" }}</strong>
            <small>{{ recorderState.encounterId || "Waiting for encounter" }}</small>
          </div>
          <label>
            Session ID
            <input v-model="recorderState.sessionId" :disabled="recorderState.recording" />
          </label>
          <div class="button-row">
            <button type="button" :disabled="recorderState.recording" @click="startRecording">
              Start Recording
            </button>
            <button class="danger" type="button" :disabled="!recorderState.recording" @click="stopRecording">
              Stop
            </button>
          </div>
          <dl class="meta-grid">
            <div>
              <dt>Connection</dt>
              <dd>{{ recorderState.connected ? "Connected" : "Closed" }}</dd>
            </div>
            <div>
              <dt>Encounter</dt>
              <dd>{{ recorderState.encounterId || "Pending" }}</dd>
            </div>
          </dl>
          <textarea :value="recorderState.transcript" readonly placeholder="Live transcript appears here"></textarea>
          <div class="event-log">
            <p v-for="event in recorderState.events" :key="event.id">
              <span>{{ event.at }}</span>{{ event.message }}
            </p>
          </div>
        </section>

        <section class="panel detail-panel">
          <div class="panel-header">
            <h2>Encounter Detail</h2>
            <button type="button" :disabled="!selectedId || status.loading" @click="createSoapNote">
              Generate SOAP
            </button>
          </div>
          <dl v-if="selectedEncounter" class="meta-grid">
            <div>
              <dt>Status</dt>
              <dd>{{ selectedEncounter.status }}</dd>
            </div>
            <div>
              <dt>SOAP</dt>
              <dd>{{ selectedEncounter.has_soap_note ? "Ready" : "Missing" }}</dd>
            </div>
            <div>
              <dt>Coding</dt>
              <dd>{{ selectedEncounter.has_coding_recommendation ? "Ready" : "Missing" }}</dd>
            </div>
          </dl>
          <div v-if="selectedEncounter" class="artifact-grid">
            <article>
              <h3>Transcript</h3>
              <pre>{{ selectedEncounter.transcript || "No final transcript" }}</pre>
            </article>
            <article>
              <h3>SOAP Note</h3>
              <pre>{{ selectedEncounter.soap_note ? JSON.stringify(selectedEncounter.soap_note, null, 2) : "No SOAP note" }}</pre>
            </article>
          </div>
          <p v-else class="empty">Select or record an encounter to begin.</p>
        </section>

        <section class="panel recommendation-panel">
          <div class="panel-header">
            <h2>Coding Recommendation</h2>
            <button type="button" :disabled="!selectedId || status.loading" @click="createRecommendation">
              Recommend
            </button>
          </div>
          <div class="form-grid">
            <label>
              Setting
              <input v-model="recommendForm.setting" />
            </label>
            <label>
              Patient Type
              <input v-model="recommendForm.patient_type" />
            </label>
            <label>
              Service Date
              <input v-model="recommendForm.service_date" type="date" />
            </label>
            <label>
              Total Time
              <input v-model="recommendForm.total_time_minutes" type="number" min="0" placeholder="Minutes" />
            </label>
          </div>
          <div class="form-grid three">
            <label>
              MDM Problems
              <input v-model="recommendForm.mdm_problems" placeholder="Example: moderate" />
            </label>
            <label>
              MDM Data
              <input v-model="recommendForm.mdm_data" placeholder="Example: labs reviewed" />
            </label>
            <label>
              MDM Risk
              <input v-model="recommendForm.mdm_risk" placeholder="Example: prescription management" />
            </label>
          </div>
          <label class="checkbox-row">
            <input v-model="recommendForm.same_day_separate_em" type="checkbox" />
            Same-day separate E/M service
          </label>
          <article>
            <h3>Latest Recommendation</h3>
            <pre>{{ selectedEncounter?.coding_recommendation ? JSON.stringify(selectedEncounter.coding_recommendation, null, 2) : "No coding recommendation" }}</pre>
          </article>
        </section>
      </div>
    </section>
    </section>
  </main>
</template>
