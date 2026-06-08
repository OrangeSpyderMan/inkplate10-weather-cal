const CALENDAR_URL = "/outputs/inkplate10-portrait/calendar.png";
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

  nextImage.src = `${CALENDAR_URL}?t=${Date.now()}`;
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
