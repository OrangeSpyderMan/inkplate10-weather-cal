#include "lib.h"

// RTC epoch of the last time we booted.
RTC_DATA_ATTR time_t lastBootTime = 0;
// RTC epoch of the last time deep sleep was initiated.
RTC_DATA_ATTR time_t lastSleepTime = 0;

// remote mqtt logger
WiFiClient espClient;
PubSubClient client(espClient);
MqttLogger mqttLogger(client, "", MqttLoggerMode::SerialOnly);
// queue to store messages to publish once mqtt connection is established.
cppQueue logQ(LOG_MSG_MAX_LEN, LOG_QUEUE_MAX_ENTRIES, FIFO, true);
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
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, pass);
    logf(LOG_INFO, "connecting to WiFi SSID %s...", ssid);

    // Retry until success or give up
    int attempts = 0;
    while (attempts++ <= retries && WiFi.status() != WL_CONNECTED)
    {
        logf(LOG_DEBUG, "connection attempt #%d...", attempts);
        delay(2500);
    }

    // If still not connected, error with timeout.
    if (WiFi.status() != WL_CONNECTED)
    {
        return ESP_ERR_TIMEOUT;
    }
    // Print the IP address
    logf(LOG_INFO, "IP address: %s", WiFi.localIP().toString().c_str());

    return ESP_OK;
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
    if (!client.connected())
    {
        logQ.push(logMsg);
    }
    else if (logQ.getCount() > 0)
    {
        char tempBuf[LOG_MSG_MAX_LEN];
        mqttLogger.setMode(MqttLoggerMode::MqttOnly);
        while (!logQ.isEmpty())
        {
            if (logQ.pop(tempBuf))
            {
                mqttLogger.println(tempBuf);
            }
        }
        mqttLogger.setMode(MqttLoggerMode::MqttAndSerial);
    }

    mqttLogger.println(logMsg);
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

    if (!waitForSync())
    {
        return ESP_ERR_ENTP;
    }
    myTz.setLocation(F(timezoneName));

    updateNTP();
    // Sync RTC with NTP time
    // time_t nowTime = now();
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
    log(LOG_NOTICE, "deep sleeping in 5 seconds");
    delay(5000);

    lastSleepTime = rtcTime;

    log(LOG_NOTICE, "Shutdown is NOW!");
    log(LOG_DEBUG, "Disconnect WiFi...");
    WiFi.disconnect();
    log(LOG_DEBUG, "Turn off WiFi...");
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
