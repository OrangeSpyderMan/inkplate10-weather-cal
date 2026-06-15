#include <ArduinoJson.h>

#include "config.h"
#include "lib.h"

void setup()
{
    Serial.begin(115200);
    // Init inkplate board.
    board.begin();
    // Keep startup/configuration errors readable for existing installations.
    board.setRotation(CONFIG_DEFAULT_DISPLAY_ROTATION);

    // Set clock from RTC
    board.rtc.getRtcData();
    time_t bootTime = board.rtc.getEpoch();
    setTime(bootTime);

    logf(LOG_DEBUG, "boot time: %s", dateTime(bootTime, RFC3339).c_str());
    logf(LOG_DEBUG, "firmware version: %s", FIRMWARE_VERSION);

    const char *wakeCause = "reset";
    esp_sleep_wakeup_cause_t wakeup_reason = esp_sleep_get_wakeup_cause();
    switch (wakeup_reason)
    {
    case ESP_SLEEP_WAKEUP_EXT0:
        wakeCause = "rtc_io";
        logf(LOG_DEBUG, "wakeup caused by external signal using RTC_IO.");
        board.rtc.clearAlarmFlag();
        break;
    case ESP_SLEEP_WAKEUP_EXT1:
        wakeCause = "rtc_control";
        logf(LOG_DEBUG, "wakeup caused by external signal using RTC_CNTL.");
        break;
    case ESP_SLEEP_WAKEUP_TIMER:
        wakeCause = "timer";
        logf(LOG_DEBUG, "wakeup caused by timer.");
        break;
    case ESP_SLEEP_WAKEUP_TOUCHPAD:
        wakeCause = "touchpad";
        logf(LOG_DEBUG, "wakeup caused by touchpad.");
        break;
    case ESP_SLEEP_WAKEUP_ULP:
        wakeCause = "ulp";
        logf(LOG_DEBUG, "wakeup caused by ULP program.");
        break;
    default:
        log(LOG_DEBUG, "wakeup caused by RST pin or power button");
        break;
    }

    // Init err state.
    esp_err_t err = ESP_OK;

    StaticJsonDocument<CONFIG_YAML_DOCUMENT_CAPACITY> configDocument;
    RuntimeConfig config;
    if (!loadRuntimeConfig(configDocument, config))
    {
        return;
    }
    board.setRotation(config.displayRotation);
    mqttDebugEnabled = config.mqttDebugEnabled;

    logTagged(
        LOG_INFO, "WAKE", "cause=%s firmware=%s",
        wakeCause, FIRMWARE_VERSION);

    // Check the battery before starting the radio or other network work.
    float bvolt = readBatteryVoltage();
    if (bvolt <= 0.0F)
    {
        log(LOG_WARNING, "battery voltage reading is invalid");
    }
    else
    {
        logTagged(LOG_INFO, "BATTERY", "voltage=%.2fV", bvolt);
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
            sleep(config.calendarRefreshInterval);
        }

        batteryLowWarningDisplayed = false;
        if (bvolt < BATTERY_WARNING_VOLTAGE)
        {
            log(LOG_WARNING, "battery low, charge soon");
        }
    }

    // Attempt to connect to WiFi.
    err = configureWiFi(config.wifiSSID, config.wifiPass, config.wifiRetries);
    if (err == ESP_ERR_TIMEOUT)
    {
        const char *errMsg = "wifi connect timeout";
        log(LOG_ERROR, errMsg);
        String diagnostics = networkDiagnostics();
        diagnostics = appendDiagnostic(diagnostics, "SSID: ", config.wifiSSID);
        diagnostics = appendDiagnostic(
            diagnostics,
            "Retries configured: ",
            String(config.wifiRetries));
        displayError("WiFi error", errMsg, diagnostics);
        sleep(config.calendarRefreshInterval);
    }

    if (config.mqttLoggerEnabled)
    {
        err = configureMQTT(
            config.mqttLoggerBroker,
            config.mqttLoggerPort,
            config.mqttLoggerTopic,
            config.mqttLoggerClientID,
            config.mqttLoggerRetries);
        if (err != ESP_OK)
        {
            log(LOG_WARNING,
                "failed to connect remote diagnostics, fallback to serial");
        }
    }

    // Attempt to synchronize clocks with network time.
    err = configureTime(config.ntpHost, config.ntpTimezone);
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

        err = displayImage(config.calendarUrl);
        if (err != ESP_OK)
        {
            errMsg = "image display error";
            log(LOG_ERROR, errMsg);
            continue;
        }
    } while (err != ESP_OK && ++attempts <= config.calendarRetries);

    // E-ink retains the previous image, so a failed refresh should not replace
    // a valid forecast with an error screen.
    if (err != ESP_OK)
    {
        logf(LOG_ERROR, "%s after %d attempts", errMsg, attempts);
        logf(LOG_ERROR, "calendar URL: %s", config.calendarUrl);
    }

    // Deep sleep until next refresh time
    sleep(config.calendarRefreshInterval);
}

void loop() {}
