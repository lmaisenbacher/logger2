"""Multi-purpose data logging software."""
import configparser
import json
import time
import logging
import socket
import random

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

LOG = logging.getLogger()

CONFIGPATH = "config.ini"

CONF = configparser.ConfigParser()
CONF.read(CONFIGPATH)

DB_URL = CONF["Database"]["url"]
DB_BUCKET = CONF["Database"]["bucket"]
DB_ORG = CONF["Database"]["org"]
DB_TOKEN = CONF["Database"]["token"]
UPDATE_INTERVAL = int(CONF["Update"]["interval"])
TIMEOUT = int(CONF["Devices"]["timeout"])

DEVICE_CONFIG_PATH = CONF["Devices"]["configpath"]

# bucket = "Unitrap"
# org = "Unitrap"
# token = "QU3ThIPFYoXVd8hKWag_xRkrZTIUP2esSsAe-JECyWvRJwwV_qXNvqbmwfITF5gDj110fYlMCM-TGBe6vQ5xSg=="
# Store the URL of your InfluxDB instance
# url = "http://localhost:8086"

client = influxdb_client.InfluxDBClient(
   url=DB_URL,
   token=DB_TOKEN,
   org=DB_ORG
)
write_api = client.write_api(write_options=SYNCHRONOUS)

def write_value(device, channel, value):
    """Write a new measured value to the database

    :device: Configuration dict of the device
    :channel_id: Configuration dict of the measurement channel
    :value: Measured value
    """
    tags = device["tags"]
    tags.update(channel["tags"])
    if "Multiplier" in channel:
        value *= channel["Multiplier"]
    json_body = [
        {
            "measurement": device["measurement"],
            "fields": {channel["field-key"]: value},
            "tags": tags
        }
    ]
    LOG.info("Channel %d: %s", channel["DeviceChannel"], value)
    CLIENT.write_points(json_body)

def _setup_logging():
    """Configure the application logging setup."""
    logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    _setup_logging()
    while True:
        value = random.random()
        json_body = [
            {
                "measurement": "test",
                "fields": {"value": value},
                "tags": {"device": "random-number-gen"}
            }
        ]
        LOG.info("Channel: %f", value)
        write_api.write(
            DB_BUCKET, DB_ORG, json_body)

        time.sleep(UPDATE_INTERVAL)
