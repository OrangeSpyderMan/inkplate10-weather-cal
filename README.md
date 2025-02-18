# Inkplate 10 Weather Calendar

Display today's date, weather forecast and a stylised map of your city using an Inkplate 10 that can last for months on a single battery.

<img src=https://user-images.githubusercontent.com/5797356/223708925-131d7ecc-5e95-453a-b687-427b75d959dd.jpg width=800 />

- [Background](#background)
- [How it Works](#how-it-works)
- [Bill of Materials](#bill-of-materials)
- [Setup](#setup)
- [Firmware](#firmware)
  - [Building with Arduino IDE](#building-with-arduino-ide)
- [License](#license)

## Background

I was looking for a weather station for my home.  I googled a few projects, and came across [Chris Twomey's Inkplate Weather Calendar](https://github.com/chrisjtwomey/inkplate10-weather-cal).  This is a fork of that project, as I wanted to make some changes, and particularly I wasn't too bothered about having long battery life, and wanted to run the server side as a docker container on a small SBC type system that would run all the time to allow more frequent updates.  I made a few other tweaks, but it's broadly Chris' work.

## How it Works

Both a server and client and required. The main workload is in the server which allows the client to save power by not generating the image itself. The client can also be placed where it has access to your WiFi network.

<img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/ff903fe3-4576-41d1-92b5-3a374242759a width=800 />

### Client (Inkplate 10)
1. Wakes from deep sleep and attempts to connect to WiFi.
2. Attempts to get current network time and update real-time clock.
3. (Optional) Attempts to connect a MQTT topic to publish logs. This allows us to see what the ESP32 controller is doing without needing to monitor the serial connection.
4. Attempt to download the PNG image that the server is hosting.
5. Write the downloaded PNG image to SD card.
6. Read the PNG image back from SD card and write to the e-ink display.
7. Returns to deep sleep until the next scheduled wake time (eg. 24 hours).

#### Features:
  - Ultra-low power consumption:
    - approx 21µA in deep sleep
    - approx 240mA awake
    - approx 30 seconds awake time daily
  - Real-time clock for precise sleep/wake times.
  - Daylight savings time handled automatically.
  - Can publish to a MQTT topic for remote-logging.
  - Renders messages on the e-ink display for critical errors (eg. battery low, wifi connect timeout etc.).
  - Stores calendar images on SD card.
  - Reconfigure client by updating YAML file on SD card and reboot - easy!



### Server (Raspberry Pi)
1. Gets any relevant new data (ie. weather, maps).
2. Generates a HTML file using a Python HTML translator [Airium](https://pypi.org/project/airium/).
3. [Selenium](https://pypi.org/project/selenium/) then uses [Geckodriver](https://github.com/mozilla/geckodriver) to make [Firefox](https://www.mozilla.org/firefox/) is then used to turn that generated HTML file into PNG image that fits the dimensions of e-ink resolution.
4. A [Flask](https://flask.palletsprojects.com/en/2.3.x/) server is then started to serve the generated PNG image to the client.
5. (Optional) The server listens for client logs by subscribing to a MQTT topic using [Mosquitto](https://mosquitto.org/).
6. Depending on configuration the server will either shutdown, run indefinitely, or shutdown after a certain number of times the image is served.
7. A cronjob ensures the server is started at the next scheduled wake time of the client.

#### Features:
See the [server](/server) for more features.


## Bill of Materials

- **Inkplate 10 by Soldered Electronics ~€150**

  The [Inkplate 10](https://www.crowdsupply.com/soldered/inkplate-10) is an all-in-one hardware solution for something like this. It has a 9.7" 1200x825 display with integrated ESP32, real-time clock, and battery power management. You can get it either [directly from Soldered Electronics](https://soldered.com/product/soldered-inkplate-10-9-7-e-paper-board-with-enclosure-copy) or from a [UK reseller like Pimoroni](https://shop.pimoroni.com/products/inkplate-10-9-7-e-paper-display?variant=39959293591635). While it might seem pricey at first glance, a [similarly sized raw display from Waveshare](https://www.amazon.co.uk/Waveshare-Parallel-Resolution-Industrial-Instrument/dp/B07JG4SXBV) can cost the same or likely more, and you would still need to source the microcontroller, RTC, and BMS yourself.
  
- **2 GB microSD card ~€5**
  
  Whatever is the cheapest microSD card you can find, you will not likely need more than few hundred kilobytes of storage. It will be mainly used by Inkplate to cache downloaded images from the server until it needs to refresh the next day. The config file for the code will also need to be stored here.

- **3000mAh LiPo battery pack ~€10**

  Any Lithium-Ion/Polymer battery will do as long as they have a JST connector for hooking up to the Inkplate board. Some Inkplate 10's are sold with a 3000mAh battery which should give approximately 6 months of life. Here is [the battery I used](https://cdn-shop.adafruit.com/datasheets/LiIon2000mAh37V.pdf). See section on [power consumption](#power-consumption) for more info on real-world calculations.

- **CR2032 3V coin cell ~€1**

  In order to power the real-time clock for when the board needs to deep sleep. Should be easily-obtainable in any hardware or home store.
  
- **Raspberry Pi Zero W ~€40**

  To run the server, you will need to something that can run Python 3 and chromedriver. The server itself is lightweight with the only real work involved is chromedriver generating a PNG image before serving it to the client. It can also be configured to auto-shutdown when it has successfully served the image to the client. A board such as the Raspberry Pi Zero W is perfect for its low power-consumption but any computer you're happy with running 24/7 is suitable.
  
- **Black photo frame 8"x10" ~€10**

  This might be the trickiest part to source, as the card insert (also called the 'mount') needs to fit the 8"x10" frame but fit a photo closer in dimension to 5.5"x7.5" in order for just the e-ink part of the board to be in-frame.  The inkplate I bought came with a 3D printed case that looks good enough, and has ports in the right places for charging/SD card access etc and a handy (but a flaky..) on/off switch.

## Setup

Place `config.yaml` in the root directory of an SD card and connect it to your Inkplate 10 board.

```
calendar:
  url: http://localhost:8080/calendar.png
  refresh_interval: 3
  retries: 3
wifi:
  ssid: XXXX
  pass: XXXX
  retries: 6
ntp:
  host: pool.ntp.org
  timezone: Europe/Dublin
mqtt_logger:
  enabled: false
  broker: localhost
  port: 1883
  clientId: inkplate10-weather-cal
  topic: mqtt/weather-cal
  retries: 3
```

Likely parameters you'll need to change is 
- `wifi.ssid` - the SSID if your WiFi network.
- `wifi.pass` - the WiFi password.
- `calendar.url` - the hostname or IP address of your server which the client will attempt to download the image from.
- `calendar.refresh_interval` - how often you want the device to wake up and check for a new image.
- `ntp.timezone` - the timezone you live in (in "Olson" format), otherwise the client might not wake at the expected time.  
- `mqtt_logger.broker` - the hostname or IP address of your server (likely the same server as the image host).

See the [server](/server) for info on server setup.

## Firmware

### Building with Arduino IDE

The firmware can be compiled correctly on the Arduino IDE.  There is a compiled firmware copy that is shared in [Compiled Firmware](compiled_firmware).  You may be able to use this to program your board directly, but I would recommend setting up an Arduino IDE with the latest library versions and compiling a version locally. 

The below assumes you already have a working Arduino environment, configure for the Inkplate10 (with the board definition).   The documentation for that is available here :

- https://inkplate.readthedocs.io/en/latest/get-started.html#arduino

The following libraries should be installed in your Arduino IDE.  They are available in the IDE's Library Manager :
- [InkplateLibrary](https://github.com/SolderedElectronics/Inkplate-Arduino-library)
- [Arduinojson](https://arduinojson.org/?utm_source=meta&utm_medium=library.properties)
- [MQTTLogger](https://github.com/androbi-com/MqttLogger)
- [Queue](https://github.com/SMFSW/Queue)
- [StreamUtils](https://github.com/bblanchon/ArduinoStreamUtils)
- [YAMLDuino](https://github.com/tobozo/YAMLDuino)
- [ezTime](https://github.com/ropg/ezTime)


## License

All code in this repository is licensed under the MIT license.

Weather icons by [lutfix](https://www.flaticon.com/authors/lutfix) from [www.flaticon.com](https://www.flaticon.com).