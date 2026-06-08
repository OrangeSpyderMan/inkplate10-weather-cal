#include "lib.h"

// RTC epoch of the last time we booted.
RTC_DATA_ATTR time_t lastBootTime = 0;
// RTC epoch of the last time deep sleep was initiated.
RTC_DATA_ATTR time_t lastSleepTime = 0;
// Avoid redrawing the same retained low-battery screen on every wake.
RTC_DATA_ATTR bool batteryLowWarningDisplayed = false;
// Cache timezone rules across deep sleep to avoid a network lookup every wake.
RTC_DATA_ATTR char cachedTimezoneName[64] = "";
RTC_DATA_ATTR char cachedTimezonePosix[96] = "";

// remote mqtt logger
WiFiClient espClient;
PubSubClient client(espClient);
MqttLogger mqttLogger(client, "", MqttLoggerMode::SerialOnly);
// queue to store messages to publish once mqtt connection is established.
cppQueue logQ(LOG_MSG_MAX_LEN, LOG_QUEUE_MAX_ENTRIES, FIFO, true);
const char *mqttLogTopic = nullptr;
// inkplate10 board driver
Inkplate board(INKPLATE_3BIT);
// timezone store
Timezone myTz;

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
    board.display();

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

    client.setServer(broker, port);
    int attempts = 0;
    while (attempts++ <= max_retries && !client.connect(clientID))
    {
        logf(LOG_DEBUG, "MQTT connection attempt #%d...", attempts);
        delay(250);
    }

    if (!client.connected())
    {
        return ESP_ERR_TIMEOUT;
    }

    mqttLogTopic = topic;
    mqttLogger.setTopic(topic);
    mqttLogger.setMode(MqttLoggerMode::MqttAndSerial);
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

    ensureQueue(buf);
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

    ensureQueue(buf);
}

/**
  Queue or publish a diagnostic log message based on MQTT connection state.

  @param logMsg the log message.
*/
void ensureQueue(const char *logMsg)
{
    // Keep serial diagnostics independent of MQTT delivery.
    Serial.println(logMsg);

    if (!client.connected() || mqttLogTopic == nullptr)
    {
        logQ.push(logMsg);
        return;
    }

    while (!logQ.isEmpty())
    {
        char tempBuf[LOG_MSG_MAX_LEN];
        if (logQ.pop(tempBuf))
        {
            client.publish(mqttLogTopic, tempBuf, false);
        }
    }

    client.publish(mqttLogTopic, logMsg, false);
}

/**
  Connect to an NTP server and synchronize the on-board real-time clock.

  @param host the hostname of the NTP server (eg. pool.ntp.org).
  @param timezoneName the name of the timezone in Olson format (eg.
  Europe/Dublin)
  @returns the esp_err_t code:
  - ESP_OK if successful.
  - ESP_ERR_ENTP if updating the NTP client fails.
*/
esp_err_t configureTime(const char *ntpHost, const char *timezoneName)
{
    log(LOG_INFO, "configuring network time and RTC...");

    setServer(ntpHost);
    updateNTP();
    if (!waitForSync(5))
    {
        return ESP_ERR_ENTP;
    }

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

    // Sync RTC with NTP time
    time_t nowTime = myTz.now();
    board.rtc.setEpoch(nowTime);
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

    log(LOG_NOTICE, "deep sleep initiated");
    time_t rtcTime = board.rtc.getEpoch();
    logf(LOG_DEBUG, "RTC time now is %s", dateTime(rtcTime, RFC3339).c_str());

    logf(LOG_INFO, "waking in %d hours", boundedSleepHours);
    lastSleepTime = rtcTime;

    log(LOG_NOTICE, "Shutdown is NOW!");
    if (client.connected())
    {
        // PubSubClient publishes diagnostics at QoS 0. Give the TCP stack a
        // short bounded window to transmit queued packets before disconnecting.
        const unsigned long flushStarted = millis();
        while (client.connected() && millis() - flushStarted < 750)
        {
            client.loop();
            delay(10);
        }
        client.disconnect();
    }
    Serial.flush();
    WiFi.disconnect(false, false);
    WiFi.mode(WIFI_OFF);
#if !defined(EMBEDDED_CONFIG)
    log(LOG_DEBUG, "Sleep SDCard...");
    board.sdCardSleep();
#endif

    const uint64_t sleepMicroseconds = ((uint64_t)boundedSleepHours * 60 * 60 * 1000 * 1000); // Convert the Hours interval into microseconds
    logf(LOG_DEBUG, "Enable sleep timer for wakeup after %llu microseconds", sleepMicroseconds);
    esp_sleep_enable_timer_wakeup(sleepMicroseconds);
    log(LOG_NOTICE, "Sleeping...");
    esp_deep_sleep_start();
}
