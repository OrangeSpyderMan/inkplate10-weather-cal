const CALENDAR_URL = "/app/calendar.png";
const REFRESH_INTERVAL_MS = 15 * 60 * 1000;

const calendar = document.getElementById("calendar");
const status = document.getElementById("status");

function setStatus(message) {
  if (!message) {
    status.hidden = true;
    status.textContent = "";
    return;
  }

  status.hidden = false;
  status.textContent = message;
}

function refreshCalendar() {
  const nextImage = new Image();
  nextImage.decoding = "async";

  nextImage.onload = () => {
    calendar.src = nextImage.src;
    setStatus("");
  };

  nextImage.onerror = () => {
    if (!calendar.src) {
      setStatus("Calendar unavailable");
    }
  };

  // Conditional HTTP caching avoids downloading unchanged images repeatedly.
  nextImage.src = CALENDAR_URL;
}

refreshCalendar();
setInterval(refreshCalendar, REFRESH_INTERVAL_MS);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refreshCalendar();
  }
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js");
}

const liveWeather = document.getElementById("live-weather");
const locationPicker = document.getElementById("weather-location");
const conditions = document.getElementById("conditions");
const observationTime = document.getElementById("observation-time");
let lastWeather = null;
let lastLocation = null;
let weatherRequest = 0;

function paintWeather(payload) {
  const temperature = payload.current.temperature;
  const wind = payload.current.wind;
  const values = [];
  if (temperature) values.push(`${temperature.value}${temperature.unit}`);
  if (payload.current.humidity != null) values.push(`${payload.current.humidity}% humidity`);
  if (wind) values.push(`Wind ${wind.value} ${wind.unit}`);
  conditions.textContent = values.join(" · ") || "Awaiting observations";
  const metadata = payload.measurement_metadata.temperature;
  if (metadata && metadata.observed_at) {
    const observed = new Date(metadata.observed_at);
    observationTime.textContent = `${metadata.stale ? "Stale · " : ""}Measured ${observed.toLocaleString()}`;
  } else {
    observationTime.textContent = `Forecast conditions${payload.forecast.stale ? " · stale" : ""}`;
  }
}

async function refreshWeather() {
  if (document.visibilityState === "hidden" || !locationPicker.value) return;
  const generation = ++weatherRequest;
  const location = locationPicker.value;
  try {
    const response = await fetch(`/api/v1/locations/${encodeURIComponent(location)}/weather`, { cache: "no-cache" });
    if (!response.ok) throw new Error("Weather unavailable");
    const payload = await response.json();
    if (generation !== weatherRequest || location !== locationPicker.value) return;
    lastWeather = payload;
    lastLocation = location;
    paintWeather(payload);
  } catch {
    if (generation !== weatherRequest) return;
    if (lastWeather && lastLocation === location) {
      paintWeather(lastWeather);
      observationTime.textContent += " · connection unavailable";
    } else {
      conditions.textContent = "Weather unavailable";
      observationTime.textContent = "";
    }
  }
}

async function discoverLocations() {
  try {
    const response = await fetch("/api/v1/locations", { cache: "no-cache" });
    if (!response.ok) return; // Direct-provider calendar deployments have no proxy.
    const payload = await response.json();
    locationPicker.replaceChildren(...payload.locations.map(location => {
      const option = document.createElement("option");
      option.value = location.id;
      option.textContent = location.name;
      return option;
    }));
    if (!payload.locations.length) return;
    liveWeather.hidden = false;
    await refreshWeather();
  } catch {
    // Discovery will be retried when the page returns to the foreground.
  }
}

locationPicker.addEventListener("change", refreshWeather);
setInterval(refreshWeather, 30 * 1000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    if (liveWeather.hidden) discoverLocations();
    else refreshWeather();
  }
});
discoverLocations();
