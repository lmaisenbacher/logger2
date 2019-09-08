"""Multi-purpose data logging software."""
import configparser
import json
import time
import logging
import socket

import influxdb
from serial import SerialException

import pfeiffer
import pfeiffer_eth
import vacom
import keithley

LOG = logging.getLogger()

CONFIGPATH = "config.ini"

CONF = configparser.ConfigParser()
CONF.read(CONFIGPATH)

DB_HOSTNAME = CONF["Database"]["hostname"]
DB_PORT = int(CONF["Database"]["port"])
DB_NAME = CONF["Database"]["dbname"]
UPDATE_INTERVAL = int(CONF["Update"]["interval"])
TIMEOUT = int(CONF["Devices"]["timeout"])

DEVICE_CONFIG_PATH = CONF["Devices"]["configpath"]

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

def init_device(device):
    """Initialize the device object.

    :device: Configuration dict of the device to initialize
    """
    LOG.info("Trying to initialize device %s at %s", device["Model"], device["Address"])

    if device["Model"] == "Pfeiffer Vacuum TPG 261":
        try:
            device["Object"] = pfeiffer.TPG261(device["Address"])
        except SerialException as err:
            LOG.error("Could not open serial device: %s", err)
            return

    if device["Model"] == "Pfeiffer Vacuum TPG 262":
        try:
            device["Object"] = pfeiffer.TPG262(device["Address"])
        except SerialException as err:
            LOG.error("Could not open serial device: %s", err)
            return

    if device["Model"] == "VACOM COLDION CU-100":
        try:
            device["Object"] = vacom.CU100(device["Address"])
        except SerialException as err:
            LOG.error("Could not open serial device: %s", err)
            return

    if device["Model"] == "Pfeiffer Vacuum TPG 366":
        try:
            device["Object"] = pfeiffer_eth.TPG366(device["Address"], timeout=TIMEOUT)
        except socket.error as err:
            LOG.error("Could not connect to TCP/IP device: %s", err)
            return

    if device["Model"] == "Keithley 2001 Multimeter":
        device["Object"] = keithley.Multimeter(device["Address"])
        # Set up device parameters
        device["Object"].setNrMeasurements(device["NAverages"])
        device["Object"].setAveragingState(device["Averaging"])
        device["Object"].setNrPLCycles(device["NPLC"])

def _setup_logging():
    """Configure the application logging setup."""
    logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    CLIENT = influxdb.InfluxDBClient(DB_HOSTNAME, DB_PORT)
    CLIENT.switch_database(DB_NAME)
    _setup_logging()
    with open(DEVICE_CONFIG_PATH) as device_config:
        DEVICES = json.load(device_config)
    for current_device in DEVICES:
        init_device(current_device)
    while True:
        for current_device in DEVICES:
            LOG.info("Reading device: %s at %s", current_device["Model"],
                     current_device["Address"])
            if current_device["ParallelReadout"]:
                try:
                    readings = current_device["Object"].get_values()
                except (ValueError, IOError) as err:
                    LOG.error("Could not get measurement values. Error: %s", err)
                    continue
                for current_channel, value in readings.items():
                    # This searches for a channel with matching DeviceChannel in the device
                    # config.
                    channel_information_dict = next((x for x in current_device["Channels"]
                                                     if x["DeviceChannel"] == current_channel),
                                                    None)
                    if channel_information_dict is None:
                        LOG.warning("Channel %s of device %s not configured.", current_channel,
                                    current_device["Name"])
                    else:
                        write_value(current_device, channel_information_dict, value)
            else:
                for current_channel in current_device["Channels"]:
                    try:
                        measured_value = current_device["Object"].get_value(
                            current_channel["DeviceChannel"])
                    except (ValueError, IOError) as err:
                        LOG.error("Could not get measurement value. Error: %s", err)
                        continue
                    write_value(current_device, current_channel, measured_value)

        time.sleep(UPDATE_INTERVAL)
