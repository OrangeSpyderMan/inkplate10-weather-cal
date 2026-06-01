#include <ArduinoJson.h>
#include <ArduinoYaml.h>
#include <StreamUtils.h>

#include "lib.h"

bool isMissingConfigValue(const char *value)
{
    return value == nullptr || value[0] == '\0';
}

void failConfig(const char *msg)
{
    log(LOG_ERROR, msg);
    displayMessage(msg);
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

    // Init storage.
    if (!board.sdCardInit())
    {
        const char *errMsg = "SD card init failure";
        log(LOG_ERROR, errMsg);
        displayMessage(errMsg);
        sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    }

    // Attempt to get config yaml file.
    SdFat &sd = board.getSdFat();
    File file = sd.open(CONFIG_FILE_PATH, FILE_READ);
    if (!file)
    {
        const char *errMsg = "Failed to open config file";
        logf(LOG_ERROR, errMsg);
        displayMessage(errMsg);
        sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    }

    // Attempt to parse yaml file.
    StaticJsonDocument<768> doc;
    ReadBufferingStream bufferedFile(file, 64);
    DeserializationError dse = deserializeYml(doc, bufferedFile);
    if (dse)
    {
        const char *errMsg = "Failed to load config from file";
        logf(LOG_ERROR, "failed to deserialize YAML: %s", dse.c_str());
        displayMessage(errMsg);
        sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    }
    file.close();

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

    // Remote logging config.
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

    // Attempt to connect to WiFi.
    err = configureWiFi(wifiSSID, wifiPass, wifiRetries);
    if (err == ESP_ERR_TIMEOUT)
    {
        const char *errMsg = "wifi connect timeout";
        log(LOG_ERROR, errMsg);
        displayMessage(errMsg);
        sleep(calendarRefreshInterval);
    }

    if (mqttLoggerEnabled)
    {
        // Attempt to connect to MQTT broker for remote logging.
        err = configureMQTT(mqttLoggerBroker, mqttLoggerPort, mqttLoggerTopic,
                            mqttLoggerClientID, mqttLoggerRetries);
        if (err == ESP_ERR_TIMEOUT)
        {
            log(LOG_WARNING,
                "failed to connect remote logging, fallback to serial");
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

    // Read battery voltage.
    float bvolt = board.readBattery();
    logf(LOG_INFO, "battery voltage: %sv", String(bvolt, 2).c_str());

    if (bvolt > 0.0)
    {
        if (bvolt < 3.1)
        {
            log(LOG_NOTICE, "battery near empty! - sleeping until charged");
            displayMessage("Battery empty, please charge!");
            // Sleep instead of proceeding when battery is too low.
            sleep(calendarRefreshInterval);
        }
        else if (bvolt < 3.3)
        {
            log(LOG_WARNING, "battery low, charge soon!");
        }
        else
        {
            const char *bstat = (bvolt < 3.6) ? "below" : "above";
            logf(LOG_INFO, "battery approx %s 50%% capacity", bstat);
        }
    }
    else
    {
        log(LOG_WARNING, "problem detecting battery voltage");
    }

    // Reset err state.
    err = ESP_FAIL;
    const char *errMsg;
    int attempts = 0;
    do
    {
        logf(LOG_DEBUG, "calendar refresh attempt #%d", attempts + 1);

        err = downloadFile(calendarUrl, CALENDAR_IMAGE_SIZE, CALENDAR_RW_PATH);
        if (err != ESP_OK)
        {
            errMsg = "file download error";
            log(LOG_ERROR, errMsg);
            continue;
        }

        err = displayImage(CALENDAR_RW_PATH);
        if (err != ESP_OK)
        {
            errMsg = "image display error";
            log(LOG_ERROR, errMsg);
            continue;
        }
    } while (err != ESP_OK && ++attempts <= calendarRetries);

    // If we were not successfully, print the error msg to the inkplate display.
    if (err != ESP_OK)
    {
        displayMessage(errMsg);
    }

    // Deep sleep until next refresh time
    sleep(calendarRefreshInterval);
}

void loop() {}
