const STATUS_URL = "/api/v1/status";
const POLL_INTERVAL_MS = 10 * 1000;
let lastPayload = null;

function text(id, value) {
  document.getElementById(id).textContent = value ?? "-";
}

function yesNo(value) {
  return value ? "Yes" : "No";
}

function timestamp(value) {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(undefined, { timeZoneName: "short" });
}

function localTimeZone() {
  const options = Intl.DateTimeFormat().resolvedOptions();
  const zone = options.timeZone || "Browser local time";
  const offset = new Intl.DateTimeFormat(undefined, {
    timeZoneName: "longOffset",
  })
    .formatToParts(new Date())
    .find((part) => part.type === "timeZoneName")?.value;
  return offset && offset !== zone ? `${zone} (${offset})` : zone;
}

function render(payload) {
  lastPayload = payload;
  const producer = payload.producer || {};
  const runtime = payload.runtime || {};
  const providers = payload.providers || {};
  const readiness = payload.readiness || {};
  const mqtt = payload.mqtt || {};
  const diagnostic = payload.inkplate?.latest_diagnostic;
  const state = producer.state || "unavailable";
  const stateElement = document.getElementById("state");

  stateElement.dataset.state = state;
  stateElement.textContent = state.replaceAll("_", " ");
  text("version", runtime.version);
  text("mode", producer.mode);
  text("time-zone", localTimeZone());
  text("forecast-provider", providers.forecast);
  text("realtime-provider", providers.realtime);
  text("cycle-started", timestamp(producer.cycle_started_at));
  text("last-success", timestamp(producer.last_success_at));
  text("last-failure", timestamp(producer.last_failure_at));
  text("next-refresh", timestamp(producer.next_refresh_at));
  text("weather-generated", timestamp(payload.weather?.generated_at));
  text("snapshot-ready", yesNo(readiness.snapshot));
  text("cycle-complete", yesNo(readiness.producer_cycle_complete));
  text("mqtt-enabled", yesNo(mqtt.enabled));
  text("mqtt-publish", timestamp(mqtt.last_publish_at));
  text(
    "mqtt-result",
    mqtt.last_publish_success == null
      ? "-"
      : mqtt.last_publish_success ? "Success" : `Failed: ${mqtt.last_error}`,
  );
  text(
    "inkplate-diagnostic-time",
    diagnostic ? timestamp(diagnostic.received_at) : "-",
  );
  text("inkplate-diagnostic-topic", diagnostic?.topic);
  text(
    "inkplate-diagnostic-message",
    diagnostic
      ? `${diagnostic.message}${diagnostic.truncated ? "\n[truncated]" : ""}`
      : "No diagnostic received",
  );
  text("updated", `Updated ${timestamp(payload.updated_at)}`);

  const outputs = document.getElementById("outputs");
  outputs.replaceChildren();
  Object.entries(readiness.outputs || {}).forEach(([name, ready]) => {
    const row = document.createElement("div");
    row.className = "output";
    const label = document.createElement("span");
    const result = document.createElement("strong");
    label.textContent = name;
    result.textContent = ready ? "Ready" : "Not ready";
    row.append(label, result);
    outputs.append(row);
  });

  const errorPanel = document.getElementById("error-panel");
  errorPanel.hidden = !payload.error;
  if (payload.error) {
    text(
      "error-message",
      `${payload.error.stage}: ${payload.error.type}: ${payload.error.message}`,
    );
  }
}

async function refresh() {
  try {
    const response = await fetch(STATUS_URL, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    render(payload);
    text("connection", "");
  } catch (error) {
    text("connection", `Status refresh failed: ${error.message}`);
    if (!lastPayload) {
      const state = document.getElementById("state");
      state.dataset.state = "unavailable";
      state.textContent = "Unavailable";
    }
  }
}

refresh();
setInterval(refresh, POLL_INTERVAL_MS);
