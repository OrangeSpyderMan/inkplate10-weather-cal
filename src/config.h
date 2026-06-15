#ifndef CONFIG_H
#define CONFIG_H

#include <ArduinoJson.h>

// Sized for the documented configuration fields plus moderate MQTT and URL
// growth. Review this capacity whenever fields are added to RuntimeConfig.
constexpr size_t CONFIG_YAML_DOCUMENT_CAPACITY = 768;
// SD configuration is parsed through a small fixed buffer to avoid byte-at-a-
// time reads without retaining a large buffer while the radio is active.
constexpr size_t CONFIG_SD_READ_BUFFER_SIZE = 64;

struct RuntimeConfig
{
    int displayRotation;
    const char *calendarUrl;
    int calendarRetries;
    int calendarRefreshInterval;
    const char *wifiSSID;
    const char *wifiPass;
    int wifiRetries;
    const char *ntpHost;
    const char *ntpTimezone;
    bool mqttLoggerEnabled;
    bool mqttDebugEnabled;
    const char *mqttLoggerBroker;
    int mqttLoggerPort;
    const char *mqttLoggerClientID;
    const char *mqttLoggerTopic;
    int mqttLoggerRetries;
};

bool loadRuntimeConfig(
    StaticJsonDocument<CONFIG_YAML_DOCUMENT_CAPACITY> &document,
    RuntimeConfig &config);

#endif
