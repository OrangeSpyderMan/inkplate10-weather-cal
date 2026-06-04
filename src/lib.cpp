#include "lib.h"

// RTC epoch of the last time we booted.
RTC_DATA_ATTR time_t lastBootTime = 0;
// RTC epoch of the last time deep sleep was initiated.
RTC_DATA_ATTR time_t lastSleepTime = 0;

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
  Download a file at a given URL. Store the file on disk at a given path.

  @param url the URL of the file to download.
  @param size the size of the file to download.
  @param retries the number of download attempts to make before returning an
  error.
  @returns the esp_err_t code:
  - ESP_OK if successful.
  - ESP_ERR_TIMEOUT if number of retries is exceeded without success.
*/
esp_err_t downloadFile(const char *url, int32_t size, const char *filePath)
{
    logf(LOG_INFO, "downloading file at URL %s", url);

    // Download file from URL
    uint8_t *buf = board.downloadFile(url, &size);
    if (!buf)
    {
        return ESP_ERR_EDL;
    }

    logf(LOG_INFO, "writing file to path %s", filePath);
    SdFat &sd = board.getSdFat();

    // Write image buffer to SD card
    if (sd.exists(filePath))
    {
        sd.remove(filePath);
    }

    File sdfile = sd.open(filePath, FILE_WRITE);
    if (!sdfile)
    {
        free(buf);
        return ESP_ERR_EFILEW;
    }

    size_t written = sdfile.write(buf, size);
    sdfile.close();
    free(buf);
    if (written != (size_t)size)
    {
        return ESP_ERR_EFILEW;
    }

    return ESP_OK;
}

/**
  Draw an image to the display.

  @param filePath the path of the file on disk.
  error.
  @returns the esp_err_t code:
  - ESP_OK if successful.
  - ESP_ERR_EDL if download file fails.
  - ESP_ERR_EFILEW if writing file to filePath fails.
*/
esp_err_t displayImage(const char *filePath)
{
    logf(LOG_INFO, "drawing image from path: %s", filePath);

    board.clearDisplay();
    if (!board.image.draw(filePath, 0, 0, false, true))
    {
        return ESP_ERR_EDRAW;
    }
    board.display();

    return ESP_OK;
}

/**
  Draw an message to the display. The error message is drawn in the top-left
  corner of the display. Error message will overlay previously drawn image.

  @param msg the message to display.
  error.
*/
void displayMessage(const char *msg)
{
    board.setTextSize(4);
    board.setTextColor(1, 0);
    board.setTextWrap(true);
    board.setCursor(0, 0);
    board.print(msg);
    board.display();
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

    Serial.println(buf);
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

    Serial.println(buf);
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
    log(LOG_DEBUG, "Sleep SDCard...");
    board.sdCardSleep();

    const uint64_t sleepMicroseconds = ((uint64_t)boundedSleepHours * 60 * 60 * 1000 * 1000); // Convert the Hours interval into microseconds
    logf(LOG_DEBUG, "Enable sleep timer for wakeup after %llu microseconds", sleepMicroseconds);
    esp_sleep_enable_timer_wakeup(sleepMicroseconds);
    log(LOG_NOTICE, "Sleeping...");
    esp_deep_sleep_start();
}
