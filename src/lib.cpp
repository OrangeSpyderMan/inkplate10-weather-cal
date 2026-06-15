#include <ArduinoJson.h>
#include <ctype.h>

#include "lib.h"

// RTC epoch of the last time we booted.
RTC_DATA_ATTR time_t lastBootTime = 0;
// RTC epoch of the last time deep sleep was initiated.
RTC_DATA_ATTR time_t lastSleepTime = 0;
// Avoid redrawing the same retained low-battery screen on every wake.
RTC_DATA_ATTR bool batteryLowWarningDisplayed = false;
// Avoid driving the panel repeatedly for the same retained error screen.
RTC_DATA_ATTR uint32_t displayedErrorSignature = 0;
// Cache timezone rules across deep sleep to avoid a network lookup every wake.
RTC_DATA_ATTR char cachedTimezoneName[64] = "";
RTC_DATA_ATTR char cachedTimezonePosix[96] = "";
// RTC epoch of the last successful NTP synchronization.
RTC_DATA_ATTR time_t lastNtpSyncTime = 0;
// SHA-256 of the last image successfully driven to the panel.
RTC_DATA_ATTR char displayedCalendarSignature[65] = "";

// Remote MQTT diagnostics.
esp_mqtt_client_handle_t mqttClient = nullptr;
volatile bool mqttConnected = false;
bool mqttDebugEnabled = false;
// queue to store messages to publish once mqtt connection is established.
cppQueue logQ(LOG_MSG_MAX_LEN, LOG_QUEUE_MAX_ENTRIES, FIFO, true);
const char *mqttLogTopic = nullptr;
// inkplate10 board driver
Inkplate board(INKPLATE_3BIT);
// timezone store
Timezone myTz;

namespace
{
constexpr time_t NTP_RESYNC_INTERVAL_SECONDS = 24 * 60 * 60;
constexpr time_t MIN_VALID_RTC_EPOCH = 1577836800; // 2020-01-01T00:00:00Z

uint32_t errorSignature(
    const char *title,
    const char *detail,
    const String &diagnostics)
{
    uint32_t hash = 2166136261UL;
    const char *values[] = {title, detail, diagnostics.c_str()};
    for (const char *value : values)
    {
        if (value != nullptr)
        {
            while (*value != '\0')
            {
                hash ^= static_cast<uint8_t>(*value++);
                hash *= 16777619UL;
            }
        }
        hash ^= 0xFF;
        hash *= 16777619UL;
    }
    return hash;
}
} // namespace

esp_err_t handleMQTTEvent(esp_mqtt_event_handle_t event)
{
    switch (event->event_id)
    {
    case MQTT_EVENT_CONNECTED:
        mqttConnected = true;
        break;
    case MQTT_EVENT_DISCONNECTED:
    case MQTT_EVENT_ERROR:
        mqttConnected = false;
        break;
    default:
        break;
    }

    return ESP_OK;
}

/**
  Connect to a WiFi network in Station Mode.

  @param ssid the network SSID.
  @param pass the network password.
  @param retries the number of connection attempts to make before returning an
  error.
  @returns the esp_err_t code:
  - ESP_OK if successful.
  - ESP_ERR_TIMEOUT if number of retries is exceeded without success.
*/
esp_err_t configureWiFi(const char *ssid, const char *pass, int retries)
{
    WiFi.persistent(false);
    WiFi.setAutoReconnect(false);
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, pass);
    logf(LOG_INFO, "connecting to WiFi SSID %s...", ssid);

    // Preserve the configured 2.5-second retry windows, but poll frequently so
    // a quick association does not always incur the full delay.
    for (int attempt = 0; attempt <= retries; ++attempt)
    {
        logf(LOG_DEBUG, "connection attempt #%d...", attempt + 1);
        const unsigned long started = millis();
        while (WiFi.status() != WL_CONNECTED &&
               millis() - started < 2500)
        {
            delay(100);
        }
        if (WiFi.status() == WL_CONNECTED)
        {
            break;
        }
    }

    // If still not connected, error with timeout.
    if (WiFi.status() != WL_CONNECTED)
    {
        return ESP_ERR_TIMEOUT;
    }
    // Print the IP address
    logf(LOG_INFO, "IP address: %s", WiFi.localIP().toString().c_str());
    // Network transfers temporarily disable modem sleep when they need maximum
    // throughput; keep it enabled between those transfers.
    WiFi.setSleep(true);

    return ESP_OK;
}

float readBatteryVoltage()
{
    float samples[BATTERY_CONFIRMATION_SAMPLES];
    samples[0] = board.readBattery();

    if (samples[0] < BATTERY_VALID_MIN_VOLTAGE ||
        samples[0] > BATTERY_VALID_MAX_VOLTAGE)
    {
        return 0.0F;
    }

    if (samples[0] >= BATTERY_WARNING_VOLTAGE)
    {
        return samples[0];
    }

    int validSamples = 1;
    for (int i = 1; i < BATTERY_CONFIRMATION_SAMPLES; ++i)
    {
        float sample = board.readBattery();
        if (sample >= BATTERY_VALID_MIN_VOLTAGE &&
            sample <= BATTERY_VALID_MAX_VOLTAGE)
        {
            samples[validSamples++] = sample;
        }
    }
    if (validSamples < 3)
    {
        return 0.0F;
    }

    // Insertion sort is sufficient for this small fixed-size sample set.
    for (int i = 1; i < validSamples; ++i)
    {
        float value = samples[i];
        int position = i - 1;
        while (position >= 0 && samples[position] > value)
        {
            samples[position + 1] = samples[position];
            --position;
        }
        samples[position + 1] = value;
    }

    return samples[validSamples / 2];
}

/**
  Draw an image directly from a URL.

  @param url the URL of the image.
  @returns the esp_err_t code:
  - ESP_OK if successful.
  - ESP_ERR_EDRAW if downloading or drawing the image fails.
*/
esp_err_t displayImage(const char *url)
{
    logf(LOG_INFO, "drawing image from URL: %s", url);

    board.clearDisplay();
    if (!board.image.draw(url, 0, 0, false, true))
    {
        return ESP_ERR_EDRAW;
    }

    // The image is now decoded in the framebuffer. The radio is not needed
    // while the comparatively slow e-paper waveform drives the panel.
    logTagged(LOG_INFO, "REFRESH", "status=ready");
    log(LOG_DEBUG, "shutting down network before display refresh");
    shutdownNetwork();
    board.display();
    displayedErrorSignature = 0;

    return ESP_OK;
}

esp_err_t fetchCalendarSignature(
    const char *url,
    char *signature,
    size_t signatureSize)
{
    if (url == nullptr || url[0] == '\0' || signatureSize < 65)
    {
        return ESP_ERR_EMANIFEST;
    }

    HTTPClient http;
    http.setConnectTimeout(3000);
    http.setTimeout(3000);
    http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
    if (!http.begin(url))
    {
        return ESP_ERR_EMANIFEST;
    }

    const int httpCode = http.GET();
    if (httpCode != HTTP_CODE_OK)
    {
        http.end();
        return ESP_ERR_EMANIFEST;
    }

    StaticJsonDocument<256> document;
    DeserializationError error = deserializeJson(document, http.getStream());
    http.end();
    if (error)
    {
        return ESP_ERR_EMANIFEST;
    }

    const char *sha256 = document["sha256"];
    if (sha256 == nullptr || strlen(sha256) != 64)
    {
        return ESP_ERR_EMANIFEST;
    }
    for (size_t i = 0; i < 64; ++i)
    {
        if (!isxdigit(static_cast<unsigned char>(sha256[i])))
        {
            return ESP_ERR_EMANIFEST;
        }
    }

    snprintf(signature, signatureSize, "%s", sha256);
    return ESP_OK;
}

/**
  Draw a high-contrast error screen to the display.

  @param title short error category.
  @param detail short human-readable error detail.
  @param diagnostics optional diagnostic lines.
  error.
*/
void displayError(const char *title, const char *detail, const String &diagnostics)
{
    const uint32_t signature = errorSignature(title, detail, diagnostics);
    if (displayedErrorSignature == signature)
    {
        log(LOG_INFO, "error screen unchanged; skipping display refresh");
        return;
    }

    const uint8_t black = 0;
    const uint8_t white = 7;
    const int margin = 24;
    const int bannerHeight = 96;

    board.clearDisplay();
    board.setTextWrap(true);

    board.fillRect(0, 0, board.width(), bannerHeight, black);
    board.setTextColor(white, black);
    board.setTextSize(3);
    board.setCursor(margin, 28);
    board.print("Weather calendar error");

    board.setTextColor(black, white);
    board.setTextSize(3);
    board.setCursor(margin, bannerHeight + 28);
    board.println(title);

    board.setTextSize(2);
    board.setCursor(margin, bannerHeight + 96);
    board.println(detail);

    if (diagnostics.length() > 0)
    {
        board.println();
        board.println("Diagnostics:");
        board.println(diagnostics);
    }

    board.display();
    displayedErrorSignature = signature;
}

void displayError(const char *title, const char *detail)
{
    displayError(title, detail, String());
}

/**
  Draw a high-contrast message to the display.

  @param msg the message to display.
  error.
*/
void displayMessage(const char *msg)
{
    displayError("Error", msg);
}

/**
  Connect to a MQTT broker for remote diagnostic logging.

  @param broker the hostname of the MQTT broker.
  @param port the port of the MQTT broker.
  @param topic the topic to publish logs to.
  @param clientID the name of the logger client to appear as.
  @param max_retries the number of connection attempts to make before fallback
  to serial-only logging.
  @returns the esp_err_t code:
  - ESP_OK if successful.
  - ESP_ERR_TIMEOUT if number of retries is exceeded without success.
*/
esp_err_t configureMQTT(const char *broker, int port, const char *topic,
                        const char *clientID, int max_retries)
{
    log(LOG_INFO, "configuring remote MQTT diagnostics...");

    esp_mqtt_client_config_t mqttConfig = {};
    mqttConfig.event_handle = handleMQTTEvent;
    mqttConfig.host = broker;
    mqttConfig.port = port;
    mqttConfig.client_id = clientID;
    mqttConfig.disable_auto_reconnect = false;
    mqttConfig.reconnect_timeout_ms = 250;
    mqttConfig.network_timeout_ms = 2000;

    mqttClient = esp_mqtt_client_init(&mqttConfig);
    if (mqttClient == nullptr || esp_mqtt_client_start(mqttClient) != ESP_OK)
    {
        if (mqttClient != nullptr)
        {
            esp_mqtt_client_destroy(mqttClient);
            mqttClient = nullptr;
        }
        return ESP_FAIL;
    }

    for (int attempt = 0;
         attempt <= max_retries && !mqttConnected;
         ++attempt)
    {
        logf(LOG_DEBUG, "MQTT connection attempt #%d...", attempt + 1);
        delay(250);
    }

    if (!mqttConnected)
    {
        esp_mqtt_client_stop(mqttClient);
        esp_mqtt_client_destroy(mqttClient);
        mqttClient = nullptr;
        return ESP_ERR_TIMEOUT;
    }

    mqttLogTopic = topic;
    logf(LOG_INFO, "connected to MQTT broker %s:%d", broker, port);

    return ESP_OK;
}

String wifiStatusName(wl_status_t status)
{
    switch (status)
    {
    case WL_CONNECTED:
        return "connected";
    case WL_NO_SHIELD:
        return "no shield";
    case WL_IDLE_STATUS:
        return "idle";
    case WL_NO_SSID_AVAIL:
        return "ssid unavailable";
    case WL_SCAN_COMPLETED:
        return "scan complete";
    case WL_CONNECT_FAILED:
        return "connect failed";
    case WL_CONNECTION_LOST:
        return "connection lost";
    case WL_DISCONNECTED:
        return "disconnected";
    default:
        return "unknown";
    }
}

String networkDiagnostics()
{
    String msg = "WiFi: ";
    msg += wifiStatusName(WiFi.status());
    if (WiFi.status() == WL_CONNECTED)
    {
        msg += "\nIP: ";
        msg += WiFi.localIP().toString();
    }

    return msg;
}

String joinDiagnostics(const String &first, const String &second)
{
    if (first.length() == 0)
    {
        return second;
    }
    if (second.length() == 0)
    {
        return first;
    }

    return first + "\n" + second;
}

String appendDiagnostic(const String &base, const String &label, const String &value)
{
    String line = label + value;
    return joinDiagnostics(base, line);
}

String batteryDiagnostics(const float voltage)
{
    String msg = "Battery: ";
    msg += String(voltage, 2);
    msg += "V";
    return msg;
}

String configDiagnostics(const char *path)
{
    String msg = "Config: ";
    msg += path;
    return msg;
}

/**
  Converts a priority into a log level prefix.

  @param pri the log level / priority of the message, see LOG_LEVEL.
  @returns the string value of the priority.
*/
String msgPrefix(uint16_t pri)
{
    const char *priority;

    switch (pri)
    {
    case LOG_CRIT:
        priority = "CRITICAL";
        break;
    case LOG_ERROR:
        priority = "ERROR";
        break;
    case LOG_WARNING:
        priority = "WARNING";
        break;
    case LOG_NOTICE:
        priority = "NOTICE";
        break;
    case LOG_INFO:
        priority = "INFO";
        break;
    case LOG_DEBUG:
        priority = "DEBUG";
        break;
    default:
        priority = "INFO";
        break;
    }

    char prefix[64];
    snprintf(prefix, sizeof(prefix), "%s - %s - ",
             myTz.dateTime(RFC3339).c_str(), priority);
    return String(prefix);
}

/**
  Log a message.

  @param pri the log level / priority of the message, see LOG_LEVEL.
  @param msg the message to log.
*/
void log(uint16_t pri, const char *msg)
{
    if (pri > LOG_LEVEL)
        return;

    String prefix = msgPrefix(pri);
    char buf[LOG_MSG_MAX_LEN];
    snprintf(buf, sizeof(buf), "%s%s", prefix.c_str(), msg);

    ensureQueue(buf, mqttDebugEnabled || pri <= LOG_WARNING);
}

/**
  Log a message with formatting.

  @param pri the log level / priority of the message, see LOG_LEVEL.
  @param fmt the format of the log message
*/
void logf(uint16_t pri, const char *fmt, ...)
{
    if (pri > LOG_LEVEL)
        return;

    String prefix = msgPrefix(pri);
    char buf[LOG_MSG_MAX_LEN];
    int prefixLen = snprintf(buf, sizeof(buf), "%s", prefix.c_str());
    if (prefixLen >= (int)sizeof(buf))
        prefixLen = sizeof(buf) - 1;

    va_list args;
    va_start(args, fmt);
    vsnprintf(buf + prefixLen, sizeof(buf) - prefixLen, fmt, args);
    va_end(args);

    ensureQueue(buf, mqttDebugEnabled || pri <= LOG_WARNING);
}

void logTagged(uint16_t pri, const char *tag, const char *fmt, ...)
{
    if (pri > LOG_LEVEL)
        return;

    char buf[LOG_MSG_MAX_LEN];
    int prefixLen = snprintf(
        buf, sizeof(buf), "%s - %s - ",
        myTz.dateTime(RFC3339).c_str(), tag);
    if (prefixLen >= (int)sizeof(buf))
        prefixLen = sizeof(buf) - 1;

    va_list args;
    va_start(args, fmt);
    vsnprintf(buf + prefixLen, sizeof(buf) - prefixLen, fmt, args);
    va_end(args);

    ensureQueue(buf, true);
}

/**
  Write a diagnostic message to serial and optionally queue or publish it over
  MQTT.

  @param logMsg the log message.
  @param mqttEligible whether the message should be sent over MQTT.
*/
void ensureQueue(const char *logMsg, bool mqttEligible)
{
    // Keep serial diagnostics independent of MQTT delivery.
    Serial.println(logMsg);

    if (!mqttEligible)
    {
        return;
    }

    if (!mqttConnected || mqttClient == nullptr || mqttLogTopic == nullptr)
    {
        logQ.push(logMsg);
        return;
    }

    while (!logQ.isEmpty())
    {
        char tempBuf[LOG_MSG_MAX_LEN];
        if (logQ.pop(tempBuf))
        {
            if (esp_mqtt_client_publish(
                    mqttClient, mqttLogTopic, tempBuf, 0, 1, 0) < 0)
            {
                logQ.push(tempBuf);
                return;
            }
        }
    }

    if (esp_mqtt_client_publish(
            mqttClient, mqttLogTopic, logMsg, 0, 1, 0) < 0)
    {
        logQ.push(logMsg);
    }
}

void shutdownNetwork()
{
    if (mqttClient != nullptr)
    {
        // QoS 1 entries remain in the outbox until the broker acknowledges
        // them. A healthy local broker normally clears this immediately.
        const unsigned long acknowledgementStarted = millis();
        while (mqttConnected &&
               esp_mqtt_client_get_outbox_size(mqttClient) > 0 &&
               millis() - acknowledgementStarted < MQTT_ACK_TIMEOUT_MS)
        {
            delay(10);
        }
        const int pendingBytes =
            esp_mqtt_client_get_outbox_size(mqttClient);
        if (pendingBytes > 0)
        {
            Serial.printf(
                "MQTT shutdown with %d unacknowledged outbox bytes\n",
                pendingBytes);
        }
        esp_mqtt_client_stop(mqttClient);
        esp_mqtt_client_destroy(mqttClient);
        mqttClient = nullptr;
        mqttConnected = false;
    }

    if (WiFi.getMode() != WIFI_OFF)
    {
        WiFi.disconnect(false, false);
        WiFi.mode(WIFI_OFF);
    }
}

/**
  Configure timezone rules and synchronize the on-board real-time clock from
  NTP when synchronization is due.

  @param host the hostname of the NTP server (eg. pool.ntp.org).
  @param timezoneName the name of the timezone in Olson format (eg.
  Europe/Dublin)
  @returns the esp_err_t code:
  - ESP_OK if the retained RTC is valid or synchronization succeeds.
  - ESP_ERR_ENTP if updating the NTP client fails.
*/
esp_err_t configureTime(const char *ntpHost, const char *timezoneName)
{
    log(LOG_INFO, "configuring network time and RTC...");

    if (strcmp(cachedTimezoneName, timezoneName) == 0 &&
        cachedTimezonePosix[0] != '\0')
    {
        myTz.setPosix(String(cachedTimezonePosix));
        log(LOG_DEBUG, "using cached timezone rules");
    }
    else
    {
        if (!myTz.setLocation(F(timezoneName)))
        {
            return ESP_ERR_ENTP;
        }
        String timezonePosix = myTz.getPosix();
        snprintf(cachedTimezoneName, sizeof(cachedTimezoneName), "%s",
                 timezoneName);
        timezonePosix.toCharArray(cachedTimezonePosix,
                                  sizeof(cachedTimezonePosix));
    }

    const time_t rtcTime = board.rtc.getEpoch();
    const bool rtcInvalid = rtcTime < MIN_VALID_RTC_EPOCH;
    const bool rtcMovedBackwards =
        lastNtpSyncTime > 0 && rtcTime < lastNtpSyncTime;
    const bool syncDue =
        lastNtpSyncTime == 0 ||
        rtcInvalid ||
        rtcMovedBackwards ||
        rtcTime - lastNtpSyncTime >= NTP_RESYNC_INTERVAL_SECONDS;
    if (!syncDue)
    {
        logf(LOG_INFO, "using RTC time; last NTP sync was %lld seconds ago",
             static_cast<long long>(rtcTime - lastNtpSyncTime));
        return ESP_OK;
    }

    setServer(ntpHost);
    updateNTP();
    if (!waitForSync(5))
    {
        return ESP_ERR_ENTP;
    }

    // Sync RTC with NTP time
    time_t nowTime = myTz.now();
    board.rtc.setEpoch(nowTime);
    lastNtpSyncTime = nowTime;
    logf(LOG_INFO, "RTC synced to %s", dateTime(nowTime, RFC3339).c_str());

    return ESP_OK;
}

/**
  Enter deep sleep.

  @param sleepHours the number of hours we should sleep for

*/
void sleep(const int sleepHours)
{
    int boundedSleepHours = sleepHours;
    if (boundedSleepHours <= 0)
    {
        boundedSleepHours = CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL;
    }
    sleepForSeconds(
        static_cast<uint32_t>(boundedSleepHours) * 60UL * 60UL);
}

void sleepForSeconds(const uint32_t sleepSeconds)
{
    uint32_t boundedSleepSeconds = sleepSeconds;
    if (boundedSleepSeconds == 0)
    {
        boundedSleepSeconds =
            CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL * 60UL * 60UL;
    }

    log(LOG_NOTICE, "deep sleep initiated");
    time_t rtcTime = board.rtc.getEpoch();
    logf(LOG_DEBUG, "RTC time now is %s", dateTime(rtcTime, RFC3339).c_str());

    logf(LOG_INFO, "waking in %lu seconds",
         static_cast<unsigned long>(boundedSleepSeconds));
    lastSleepTime = rtcTime;

    log(LOG_NOTICE, "Shutdown is NOW!");
    shutdownNetwork();
    Serial.flush();
#if !defined(EMBEDDED_CONFIG)
    log(LOG_DEBUG, "Sleep SDCard...");
    board.sdCardSleep();
#endif

    const uint64_t sleepMicroseconds =
        static_cast<uint64_t>(boundedSleepSeconds) * 1000ULL * 1000ULL;
    logf(LOG_DEBUG, "Enable sleep timer for wakeup after %llu microseconds", sleepMicroseconds);
    esp_sleep_enable_timer_wakeup(sleepMicroseconds);
    log(LOG_NOTICE, "Sleeping...");
    esp_deep_sleep_start();
}
