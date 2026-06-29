#ifndef LIB_H
#define LIB_H
#include <Inkplate.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <cppQueue.h>
#include <driver/rtc_io.h>
#include <ezTime.h>
#include <mqtt_client.h>
#include <rom/rtc.h>

#if __has_include("firmware_version.h")
#include "firmware_version.h"
#else
#define FIRMWARE_VERSION "unversioned"
#endif

#define CalendarYrToTm(Y) ((Y) - 1970)
// The number of seconds to sleep if RTC not configured correctly.
#define DEEP_SLEEP_FALLBACK_SECONDS 120
// log message entry history size
#define LOG_QUEUE_MAX_ENTRIES 10
// log message maximum length
#define LOG_MSG_MAX_LEN 128
// Maximum time to wait for QoS 1 MQTT acknowledgements before sleeping.
#define MQTT_ACK_TIMEOUT_MS 1500
// The file path on SD card to load config.
#define CONFIG_FILE_PATH "/config.yaml"
#if defined(EMBEDDED_CONFIG)
#define CONFIG_SOURCE "embedded firmware config"
#else
#define CONFIG_SOURCE CONFIG_FILE_PATH
#endif
// Fallback time to refresh.
#define CONFIG_DEFAULT_CALENDAR_DAILY_REFRESH_INTERVAL 3
// Preserve the original Inkplate 10 portrait orientation when unspecified.
#define CONFIG_DEFAULT_DISPLAY_ROTATION 1
// Battery voltage thresholds for a single-cell LiPo.
#define BATTERY_VALID_MIN_VOLTAGE 2.5F
#define BATTERY_VALID_MAX_VOLTAGE 4.4F
#define BATTERY_CRITICAL_VOLTAGE 3.1F
#define BATTERY_WARNING_VOLTAGE 3.3F
#define BATTERY_CONFIRMATION_SAMPLES 5

// Enum of errors that might be encountered.
#define ESP_ERR_ERRNO_BASE (0)
#define ESP_ERR_EDRAW (1 + ESP_ERR_ERRNO_BASE) // Draw error
#define ESP_ERR_ENTP (2 + ESP_ERR_ERRNO_BASE)  // NTP error
#define ESP_ERR_EMANIFEST (3 + ESP_ERR_ERRNO_BASE) // Manifest error

// Enum of log verbosity levels.
#define LOG_CRIT 0
#define LOG_ERROR 1
#define LOG_WARNING 2
#define LOG_NOTICE 3
#define LOG_INFO 4
#define LOG_DEBUG 5

#ifndef LOG_LEVEL
// Debug logging by default.
#define LOG_LEVEL LOG_DEBUG
#endif

// RTC epoch of the last time we booted.
extern RTC_DATA_ATTR time_t lastBootTime;
// RTC epoch of the last time deep sleep was initiated.
extern RTC_DATA_ATTR time_t lastSleepTime;
// Whether the retained e-ink display already shows the critical battery warning.
extern RTC_DATA_ATTR bool batteryLowWarningDisplayed;
// Signature of the retained error screen, or zero after a successful refresh.
extern RTC_DATA_ATTR uint32_t displayedErrorSignature;
// RTC epoch of the last successful NTP synchronization.
extern RTC_DATA_ATTR time_t lastNtpSyncTime;
// SHA-256 of the last image successfully driven to the panel.
extern RTC_DATA_ATTR char displayedCalendarSignature[65];
// Whether routine debug and informational messages should be sent over MQTT.
extern bool mqttDebugEnabled;
// The log message queue.
extern cppQueue logQ;
// The Inkplate board driver instance.
extern Inkplate board;
// The timezone object to store localised time
extern Timezone myTz;

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
esp_err_t configureWiFi(const char *ssid, const char *pass, int retries);

/**
  Draw an image directly from a URL.

  @param url the URL of the image.
  @returns the esp_err_t code:
  - ESP_OK if successful.
  - ESP_ERR_EDRAW if downloading or drawing the image fails.
*/
esp_err_t displayImage(const char *url);

/**
  Fetch the content signature for a rendered calendar output.

  @param url manifest URL.
  @param signature destination buffer for the 64-character SHA-256.
  @param signatureSize destination buffer size.
  @returns ESP_OK when a valid signature is read, otherwise ESP_ERR_EMANIFEST.
*/
esp_err_t fetchCalendarSignature(
    const char *url,
    char *signature,
    size_t signatureSize);

/**
  Stop MQTT and WiFi after delivering any outstanding diagnostics.
*/
void shutdownNetwork();

/**
  Read the battery voltage. Normal readings use one sample; low readings are
  confirmed with a median of multiple samples.

  @returns the measured battery voltage, or 0 if the reading is implausible.
*/
float readBatteryVoltage();

/**
  Draw a high-contrast error screen to the display.

  @param title short error category.
  @param detail short human-readable error detail.
  @param diagnostics optional diagnostic lines.
  error.
*/
void displayError(const char *title, const char *detail, const String &diagnostics);
void displayError(const char *title, const char *detail);

String appendDiagnostic(const String &base, const String &label, const String &value);
String batteryDiagnostics(const float voltage);
String configDiagnostics(const char *path);
String joinDiagnostics(const String &first, const String &second);
String networkDiagnostics();

/**
  Draw a high-contrast message to the display.

  @param msg the message to display.
  error.
*/
void displayMessage(const char *msg);

/**
  Configure local timezone rules and synchronize the on-board real-time clock
  from NTP when the retained synchronization interval has elapsed or the RTC is
  invalid.

  @param ntpHost the hostname of the NTP server (eg. pool.ntp.org).
  @param timezoneName the name of the timezone in Olson format (eg.
  Europe/Dublin)
  @returns the esp_err_t code:
  - ESP_OK if the retained RTC is valid or synchronization succeeds.
  - ESP_ERR_ENTP if updating the NTP client fails.
*/
esp_err_t configureTime(const char *ntpHost, const char *timezoneName);

/**
  Enter deep sleep.

  @param sleepHours the number of hours we should sleep for

*/
void sleep(const int sleepHours);

/**
  Enter deep sleep for a duration in seconds.

  @param sleepSeconds number of seconds before the timer wakeup.
*/
void sleepForSeconds(const uint32_t sleepSeconds);

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
                        const char *clientID, int max_retries);

/**
  Log a message.

  @param pri the log level / priority of the message, see LOG_LEVEL.
  @param msg the message to log.
*/
void log(uint16_t pri, const char *msg);

/**
  Log a message with formatting.

  @param pri the log level / priority of the message, see LOG_LEVEL.
  @param fmt the format of the log message
*/
void logf(uint16_t pri, const char *fmt, ...);

/**
  Log an event with a stable tag. Tagged events are always sent over MQTT when
  remote diagnostics are enabled.

  @param pri the log level / priority of the message.
  @param tag stable event tag such as WAKE, BATTERY, or REFRESH.
  @param fmt the event detail format.
*/
void logTagged(uint16_t pri, const char *tag, const char *fmt, ...);

/**
  Converts a priority into a log level prefix.

  @param pri the log level / priority of the message, see LOG_LEVEL.
  @returns the string value of the priority.
*/
String msgPrefix(uint16_t pri);

/**
  Write a diagnostic message to serial and optionally queue or publish it over
  MQTT.

  @param msg the log message
  @param mqttEligible whether the message should be sent over MQTT
*/
void ensureQueue(const char *msg, bool mqttEligible);

#endif
