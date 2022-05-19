"""Multi-purpose data logging software."""
import configparser
import json
import time
import logging
import socket

from defs import LoggerError

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

from serial import SerialException

import keysightdaq973a

logger = logging.getLogger()

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

def write_value(device, channel_id, value):
    """
    Write a new measured value to the database.

    :device: Configuration dict of the device
    :channel_id: Configuration dict of the measurement channel
    :value: Measured value
    """
    channel = device['Channels'][channel_id]
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
    logger.info("Channel %s: %s", channel_id, value)
    write_api.write(
        DB_BUCKET, DB_ORG, json_body)

def init_device(device):
    """Initialize the device object.

    :device: Configuration dict of the device to initialize
    """
    logger.info(
        "Trying to initialize device \'%s\' of type \'%s\'", device['Device'], device['Model'])

    device_instance = None
    
    if device["Model"] == "Keysight DAQ973A":
        device_instance = keysightdaq973a.Device(device)  
        
    return device_instance

def _setup_logging():
    """Configure the application logging setup."""
    logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    # Set up database connection
    client = influxdb_client.InfluxDBClient(
       url=DB_URL,
       token=DB_TOKEN,
       org=DB_ORG
    )
    write_api = client.write_api(write_options=SYNCHRONOUS)
    _setup_logging()
    with open(DEVICE_CONFIG_PATH) as device_config:
        devices = json.load(device_config)
    device_instances = []
    for i_device, device in enumerate(devices):
        device_instances.append(init_device(device))
    while True:
        for device, instance in zip(devices, device_instances):
            logger.info(
                "Reading device: \'%s\' at \'%s\'",
                device["Device"], device["Address"])
            if device["ParallelReadout"]:
                try:
                    readings = instance.get_values()
                except LoggerError as err:
                    logger.error("Could not get measurement values. Error: %s", err)
                    continue
                for channel_id, value in readings.items():
                    write_value(device, channel_id, value)
            # else:
            #     for current_channel in current_device["Channels"]:
            #         try:
            #             measured_value = current_device["Object"].get_value(
            #                 current_channel["DeviceChannel"])
            #         except (ValueError, IOError) as err:
            #             LOG.error("Could not get measurement value. Error: %s", err)
            #             continue
            #         write_value(current_device, current_channel, measured_value)

        time.sleep(UPDATE_INTERVAL)
