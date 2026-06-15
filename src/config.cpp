#include <ArduinoYaml.h>
#include <StreamUtils.h>

#include "config.h"
#include "lib.h"

#if defined(EMBEDDED_CONFIG)
#include "embedded_config.h"
#endif

namespace
{
bool isMissingConfigValue(const char *value)
{
    return value == nullptr || value[0] == '\0';
}

bool failConfig(const char *message)
{
    log(LOG_ERROR, message);
    displayError(
        "Config error",
        message,
        configDiagnostics(CONFIG_SOURCE));
    sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    return false;
}

bool failConfigLoad(const DeserializationError &error)
{
    const char *message = "Failed to load configuration";
    logf(LOG_ERROR, "failed to deserialize YAML: %s", error.c_str());
    String diagnostics = configDiagnostics(CONFIG_SOURCE);
    diagnostics = appendDiagnostic(diagnostics, "Parser: ", error.c_str());
    displayError("Config error", message, diagnostics);
    sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    return false;
}

DeserializationError loadConfigurationDocument(
    StaticJsonDocument<CONFIG_YAML_DOCUMENT_CAPACITY> &document)
{
#if defined(EMBEDDED_CONFIG)
    return deserializeYml(document, EMBEDDED_CONFIG_YAML);
#else
    if (!board.sdCardInit())
    {
        const char *message = "SD card init failure";
        log(LOG_ERROR, message);
        displayError("Storage error", message);
        sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    }

    SdFat &sd = board.getSdFat();
    File file = sd.open(CONFIG_FILE_PATH, FILE_READ);
    if (!file)
    {
        const char *message = "Failed to open config file";
        log(LOG_ERROR, message);
        displayError(
            "Config error",
            message,
            configDiagnostics(CONFIG_FILE_PATH));
        sleep(CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL);
    }

    ReadBufferingStream bufferedFile(file, CONFIG_SD_READ_BUFFER_SIZE);
    DeserializationError error = deserializeYml(document, bufferedFile);

    // Keep the explicit ordering: parsing completes, the file closes, and only
    // then is the SD card put back to sleep.
    file.close();
    board.sdCardSleep();
    return error;
#endif
}
} // namespace

bool loadRuntimeConfig(
    StaticJsonDocument<CONFIG_YAML_DOCUMENT_CAPACITY> &document,
    RuntimeConfig &config)
{
    DeserializationError error = loadConfigurationDocument(document);
    if (error)
    {
        return failConfigLoad(error);
    }

    JsonObject display = document["display"];
    config.displayRotation =
        display["rotation"] | CONFIG_DEFAULT_DISPLAY_ROTATION;
    if (config.displayRotation < 0 || config.displayRotation > 3)
    {
        return failConfig("Invalid display.rotation");
    }

    JsonObject calendar = document["calendar"];
    config.calendarUrl = calendar["url"];
    config.calendarRetries = calendar["retries"] | 3;
    config.calendarRefreshInterval =
        calendar["refresh_interval"] |
        CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL;

    JsonObject wifi = document["wifi"];
    config.wifiSSID = wifi["ssid"];
    config.wifiPass = wifi["pass"] | "";
    config.wifiRetries = wifi["retries"] | 6;

    config.ntpHost = document["ntp"]["host"];
    config.ntpTimezone = document["ntp"]["timezone"];

    JsonObject mqtt = document["mqtt_logger"];
    config.mqttLoggerEnabled = mqtt["enabled"] | false;
    config.mqttDebugEnabled = mqtt["debug"] | false;
    config.mqttLoggerBroker = mqtt["broker"];
    config.mqttLoggerPort = mqtt["port"] | 1883;
    config.mqttLoggerClientID = mqtt["clientId"];
    config.mqttLoggerTopic = mqtt["topic"];
    config.mqttLoggerRetries = mqtt["retries"] | 3;

    if (isMissingConfigValue(config.calendarUrl))
    {
        return failConfig("Missing calendar.url");
    }
    if (config.calendarRefreshInterval <= 0)
    {
        return failConfig("Invalid calendar.refresh_interval");
    }
    if (config.calendarRetries < 0)
    {
        return failConfig("Invalid calendar.retries");
    }
    if (isMissingConfigValue(config.wifiSSID))
    {
        return failConfig("Missing wifi.ssid");
    }
    if (config.wifiRetries < 0)
    {
        return failConfig("Invalid wifi.retries");
    }
    if (isMissingConfigValue(config.ntpHost))
    {
        return failConfig("Missing ntp.host");
    }
    if (isMissingConfigValue(config.ntpTimezone))
    {
        return failConfig("Missing ntp.timezone");
    }
    if (config.mqttLoggerEnabled)
    {
        if (isMissingConfigValue(config.mqttLoggerBroker))
        {
            return failConfig("Missing mqtt_logger.broker");
        }
        if (config.mqttLoggerPort <= 0)
        {
            return failConfig("Invalid mqtt_logger.port");
        }
        if (isMissingConfigValue(config.mqttLoggerClientID))
        {
            return failConfig("Missing mqtt_logger.clientId");
        }
        if (isMissingConfigValue(config.mqttLoggerTopic))
        {
            return failConfig("Missing mqtt_logger.topic");
        }
        if (config.mqttLoggerRetries < 0)
        {
            return failConfig("Invalid mqtt_logger.retries");
        }
    }

    return true;
}
