# Inkplate 10 Weather Calendar Server

A service for the weather calendar client written in Python3, backed by [Airium](https://pypi.org/project/airium/) and [Chromedriver](https://chromedriver.chromium.org/downloads).



Example 1                  | Example 2                 | Example 3
:-------------------------:|:-------------------------:|:-------------------------:
<img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/c37e6b65-a226-40d7-b1c7-cb3d72973054 width=300 /> | <img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/71958bcb-839d-447a-b671-a4cb5fbca25e width=300 /> | <img src=https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/90608c9f-c16e-4d56-9edc-13b9d85ef659 width=300 />

<img width="1044" alt="Screenshot 2023-05-17 at 01 07 53" src="https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/e02e672b-7ad0-431d-8a29-c2740857a4d7">



- Uses [Accuweather](https://developer.accuweather.com/) or [OpenWeatherMap](https://openweathermap.org/api) APIs for weather data.
- Uses Google's [StaticMaps API](https://developers.google.com/maps/documentation/maps-static/overview) to generate a static map of your area.
- Uses [Airium](https://pypi.org/project/airium/) and [Chromedriver](https://chromedriver.chromium.org/downloads) to generate HTML and PNG files for image serving.
- Uses [Flask](https://flask.palletsprojects.com/en/2.3.x/) to serve images.

## Setup 

### Accuweather API

In order to obtain an API Key, you will need to:
1. Sign up to [developer.accuweather.com](https://developer.accuweather.com/).
2. Create an app in [https://developer.accuweather.com/user/me/apps](https://developer.accuweather.com/user/me/apps).
3. Enter some details about the app's usage and purpose.
4. Generate API key.

Make sure you update the config `weather.apikey` with your generated api key and update `weather.service` to `accuweather`.

### OpenWeatherMap API

[DEPRECATED]
This provider is no longer supported in this version.  Please consider using [OpenWeatherMapv3](#openweathermapv3-api) that works with the OneCall V3 API [described here](https://openweathermap.org/api/one-call-3).

### OpenWeatherMapv3 API

In order to obtain an API Key, you will need to sign up to OpenWeatherMap and [generate an API key](https://home.openweathermap.org/api_keys).

This provides a lot more data than is displayed, but is set to display only three-hourly forecasts.  It can do hourly, but will need display format changes to work properly [TODO - portrait mode display?].  [TODO - add configuration options to determine no only the number of forecasts but also the hourly interval to use.  Currently is hardcoded to 6]

### Google StaticMaps API

<img src="https://github.com/chrisjtwomey/inkplate10-weather-cal/assets/5797356/b3f2efd0-23c0-4b9f-81e6-5684fc470ecc" width="800" />

In order to generate a static map of your area you will need to sign up to [Google's developer console](https://developers.google.com/):

1. Create a new project.
2. Go to Google Maps Platform → `Maps Static API` → `Enable`.
3. Go to `Credentials` → `Create Credentials` → `API Key`
4. After generating your API key, copy and update `google.apikey` in `config.yaml`
5. (Optional) add restriction to API Key and limit only to the `Maps Static API` service.

This will give us access to the Static Maps API service. In order to re-create the static map in the picture above, we first need to create a map style:

1. In Google Maps Platform → `Map styles` → `Create style`
2. In order to replicate the style used above, select `Import JSON` and paste the contents of [map-style.json](google/staticmaps/map-style.json) into the text field. This should replicate the map style I use.
3. Click `Save` and assign a name to the map style.

You can now use the map style to create a map ID that we can reference in our server:

1. In Google Maps Platform → `Map management` → `Create Map ID`.
2. Give the Map ID a name and make sure `map type` is set to `static`, then click `Save`.
3. Update the `associated map style` to the name of the map style created in the steps earlier.
4. Copy the `Map ID` and update the `google.staticmaps_id` field in `config.yaml`.

### Server setup

Ensure Python3 is installed on your system
```
python3 --version
Python 3.9.2
```

Download project and install dependencies
```
git clone https://github.com/chrisjtwomey/inkplate10-weather-cal.git
cd inkplate10-weather-cal
python3 -m pip install -r requirements.txt
```

Run the server manually:
```
python3 server.py
```

Run the server 9am each day:
```
crontab -e
```
Add this line:
```
0 9 * * * /usr/bin/python3 /path/to/server.py
```
`/path/to/server.py` should be updated to whatever the absolute path is to where `server.py` is on your filesystem.

## Running in Docker

In the server directory there is a docker-compose.yml and a Dockerfile that should allow the container to be created automagically.  There are a couple of things to know first.  

1. You will need a working server/config.yaml in the server directory.  An example is provided but it needs to be updated as per the instructions above and renamed to config.yaml
2. The container supplies the server directory to the container in order to allow adjustments to the config (or code...) to sync between the cloned local repo and the code on the server
3. It tries to run every hour as a cron job, but that really needs a lot more testing...

### Starting the container

This should be as simple as running the following command from the root of your cloned repository :

```
docker compose up
```
or if you don't want to attach to the running container :

```
docker compose up -d 
```


It will download the base image and apply some changes to build a virtual environment for the python modules.  It can be made to run permanently with the option `alwayson: true`in the config.yaml file (defaults to `false`).  It will then refresh the calendar image every `refreshhours` as specified also in the `server:` section of config.yaml (defaults to every 3 hours if no value provided)

There is a sample crontab called [docker-errorlog](docker-errorlog) that can be used to check the docker logs for any ERROR messages.  By default that runs every hour, on the hour but may need some local tweaking.


