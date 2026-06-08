#include <ArduinoJson.h>
#include <ArduinoYaml.h>
#include <StreamUtils.h>

#include "lib.h"

#if defined(EMBEDDED_CONFIG)
#include "embedded_config.h"
#endif

bool isMissingConfigValue(const char *value)
{
    return value == nullptr || value[0] == '\0';
}

void failConfig(const char *msg)
{
    log(LOG_ERROR, msg);
    displayError("Config error", msg, configDiagnostics(CONFIG_SOURCE));
    sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    while (true)
    {
        delay(1000);
    }
}

void setup()
{
    Serial.begin(115200);
    // Init inkplate board.
    board.begin();
    // Set board to portait mode.
    board.setRotation(1);

    // Set clock from RTC
    board.rtc.getRtcData();
    time_t bootTime = board.rtc.getEpoch();
    setTime(bootTime);

    logf(LOG_DEBUG, "boot time: %s", dateTime(bootTime, RFC3339).c_str());

    esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
    switch (wakeup_reason)
    {
    case ESP_SLEEP_WAKEUP_EXT0:
        logf(LOG_DEBUG, "wakeup caused by external signal using RTC_IO.");
        board.rtc.clearAlarmFlag();
        break;
    case ESP_SLEEP_WAKEUP_EXT1:
        logf(LOG_DEBUG, "wakeup caused by external signal using RTC_CNTL.");
        break;
    case ESP_SLEEP_WAKEUP_TIMER:
        logf(LOG_DEBUG, "wakeup caused by timer.");
        break;
    case ESP_SLEEP_WAKEUP_TOUCHPAD:
        logf(LOG_DEBUG, "wakeup caused by touchpad.");
        break;
    case ESP_SLEEP_WAKEUP_ULP:
        logf(LOG_DEBUG, "wakeup caused by ULP program.");
        break;
    default:
        log(LOG_DEBUG, "wakeup caused by RST pin or power button");
        break;
    }

    // Init err state.
    esp_err_t err = ESP_OK;

    StaticJsonDocument<768> doc;

#if defined(EMBEDDED_CONFIG)
    DeserializationError dse = deserializeYml(doc, EMBEDDED_CONFIG_YAML);
#else
    // SD mode uses the card only as a configuration source.
    if (!board.sdCardInit())
    {
        const char *errMsg = "SD card init failure";
        log(LOG_ERROR, errMsg);
        displayError("Storage error", errMsg);
        sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    }

    // Attempt to get config yaml file.
    SdFat &sd = board.getSdFat();
    File file = sd.open(CONFIG_FILE_PATH, FILE_READ);
    if (!file)
    {
        const char *errMsg = "Failed to open config file";
        logf(LOG_ERROR, errMsg);
        displayError("Config error", errMsg, configDiagnostics(CONFIG_FILE_PATH));
        sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    }

    ReadBufferingStream bufferedFile(file, 64);
    DeserializationError dse = deserializeYml(doc, bufferedFile);
#endif

    if (dse)
    {
        const char *errMsg = "Failed to load configuration";
        logf(LOG_ERROR, "failed to deserialize YAML: %s", dse.c_str());
        String diagnostics = configDiagnostics(CONFIG_SOURCE);
        diagnostics = appendDiagnostic(diagnostics, "Parser: ", dse.c_str());
        displayError("Config error", errMsg, diagnostics);
        sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    }
#if !defined(EMBEDDED_CONFIG)
    file.close();
    board.sdCardSleep();
#endif

    // Assign config values.
    JsonObject calendarCfg = doc["calendar"];
    const char *calendarUrl = calendarCfg["url"];
    int calendarRetries = calendarCfg["retries"] | 3;
    const int calendarRefreshInterval =
        calendarCfg["refresh_interval"] | CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL;

    // Wifi config.
    JsonObject wifiCfg = doc["wifi"];
    const char *wifiSSID = wifiCfg["ssid"];
    const char *wifiPass = wifiCfg["pass"] | "";
    int wifiRetries = wifiCfg["retries"] | 6;

    // NTP config.
    const char *ntpHost = doc["ntp"]["host"];
    const char *ntpTimezone = doc["ntp"]["timezone"];

    // Optional remote diagnostic logging config.
    JsonObject mqttLoggerCfg = doc["mqtt_logger"];
    bool mqttLoggerEnabled = mqttLoggerCfg["enabled"] | false;
    const char *mqttLoggerBroker = mqttLoggerCfg["broker"];
    int mqttLoggerPort = mqttLoggerCfg["port"] | 1883;
    const char *mqttLoggerClientID = mqttLoggerCfg["clientId"];
    const char *mqttLoggerTopic = mqttLoggerCfg["topic"];
    int mqttLoggerRetries = mqttLoggerCfg["retries"] | 3;

    if (isMissingConfigValue(calendarUrl))
    {
        failConfig("Missing calendar.url");
    }
    if (calendarRefreshInterval <= 0)
    {
        failConfig("Invalid calendar.refresh_interval");
    }
    if (calendarRetries < 0)
    {
        failConfig("Invalid calendar.retries");
    }
    if (isMissingConfigValue(wifiSSID))
    {
        failConfig("Missing wifi.ssid");
    }
    if (wifiRetries < 0)
    {
        failConfig("Invalid wifi.retries");
    }
    if (isMissingConfigValue(ntpHost))
    {
        failConfig("Missing ntp.host");
    }
    if (isMissingConfigValue(ntpTimezone))
    {
        failConfig("Missing ntp.timezone");
    }
    if (mqttLoggerEnabled)
    {
        if (isMissingConfigValue(mqttLoggerBroker))
        {
            failConfig("Missing mqtt_logger.broker");
        }
        if (mqttLoggerPort <= 0)
        {
            failConfig("Invalid mqtt_logger.port");
        }
        if (isMissingConfigValue(mqttLoggerClientID))
        {
            failConfig("Missing mqtt_logger.clientId");
        }
        if (isMissingConfigValue(mqttLoggerTopic))
        {
            failConfig("Missing mqtt_logger.topic");
        }
        if (mqttLoggerRetries < 0)
        {
            failConfig("Invalid mqtt_logger.retries");
        }
    }

    // Check the battery before starting the radio or other network work.
    float bvolt = readBatteryVoltage();
    if (bvolt <= 0.0F)
    {
        log(LOG_WARNING, "battery voltage reading is invalid");
    }
    else
    {
        logf(LOG_INFO, "battery voltage: %sV", String(bvolt, 2).c_str());
        if (bvolt < BATTERY_CRITICAL_VOLTAGE)
        {
            log(LOG_NOTICE, "battery critical; skipping network refresh");
            if (!batteryLowWarningDisplayed)
            {
                displayError(
                    "Battery low",
                    "Battery empty, please charge!",
                    batteryDiagnostics(bvolt));
                batteryLowWarningDisplayed = true;
            }
            sleep(calendarRefreshInterval);
        }

        batteryLowWarningDisplayed = false;
        if (bvolt < BATTERY_WARNING_VOLTAGE)
        {
            log(LOG_WARNING, "battery low, charge soon");
        }
    }

    // Attempt to connect to WiFi.
    err = configureWiFi(wifiSSID, wifiPass, wifiRetries);
    if (err == ESP_ERR_TIMEOUT)
    {
        const char *errMsg = "wifi connect timeout";
        log(LOG_ERROR, errMsg);
        String diagnostics = networkDiagnostics();
        diagnostics = appendDiagnostic(diagnostics, "SSID: ", wifiSSID);
        diagnostics = appendDiagnostic(diagnostics, "Retries configured: ", String(wifiRetries));
        displayError("WiFi error", errMsg, diagnostics);
        sleep(calendarRefreshInterval);
    }

    if (mqttLoggerEnabled)
    {
        err = configureMQTT(mqttLoggerBroker, mqttLoggerPort, mqttLoggerTopic,
                            mqttLoggerClientID, mqttLoggerRetries);
        if (err != ESP_OK)
        {
            log(LOG_WARNING,
                "failed to connect remote diagnostics, fallback to serial");
        }
    }

    // Attempt to synchronize clocks with network time.
    err = configureTime(ntpHost, ntpTimezone);
    if (err != ESP_OK)
    {
        log(LOG_WARNING, "failed to synchronize RTC with network time");
    }

    // Print some information about sleep and wake times.
    if (lastBootTime > 0)
    {
        logf(LOG_DEBUG, "last boot time: %s",
             dateTime(lastBootTime, RFC3339).c_str());
    }
    lastBootTime = bootTime;

    if (lastSleepTime > 0)
    {
        logf(LOG_INFO, "last sleep time: %s",
             dateTime(lastSleepTime, RFC3339).c_str());
    }

    // Reset err state.
    err = ESP_FAIL;
    const char *errMsg;
    int attempts = 0;
    do
    {
        logf(LOG_DEBUG, "calendar refresh attempt #%d", attempts + 1);

        err = displayImage(calendarUrl);
        if (err != ESP_OK)
        {
            errMsg = "image display error";
            log(LOG_ERROR, errMsg);
            continue;
        }
    } while (err != ESP_OK && ++attempts <= calendarRetries);

    // E-ink retains the previous image, so a failed refresh should not replace
    // a valid forecast with an error screen.
    if (err != ESP_OK)
    {
        logf(LOG_ERROR, "%s after %d attempts", errMsg, attempts);
        logf(LOG_ERROR, "calendar URL: %s", calendarUrl);
    }

    // Deep sleep until next refresh time
    sleep(calendarRefreshInterval);
}

void loop() {}
