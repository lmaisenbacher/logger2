"""

Test of database connection.
Writes random numbers to measurement "test".

"""
import configparser
import time
import logging
import random

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

LOG = logging.getLogger()

# Read config file
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

# Set up database connection
client = influxdb_client.InfluxDBClient(
   url=DB_URL,
   token=DB_TOKEN,
   org=DB_ORG
)
write_api = client.write_api(write_options=SYNCHRONOUS)

def write_value(device, channel, value):
    """
    Write a new measured value to the database.

    :device: Configuration dict of the device
    :channel_id: Configuration dict of the measurement channel
    :value: Measured value
    """
    tags = device["tags"]
    tags.update(channel.get("tags", {}))
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
    write_api.write(
        DB_BUCKET, DB_ORG, json_body)

def _setup_logging():
    """Configure the application logging setup."""
    logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    _setup_logging()
    while True:
        value = random.random()
        device = {
            "measurement": "test",
            "tags": {"device": "random-number-gen"},
            }
        channel = {
            "DeviceChannel": 1,
            "field-key": "value",
            }
        write_value(device, channel, value)

        time.sleep(UPDATE_INTERVAL)
