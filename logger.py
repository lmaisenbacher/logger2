# -*- coding: utf-8 -*-
"""
Multi-purpose data logging software.

@author: Lothar Maisenbacher/Berkeley.
"""
import configparser
import json
import time
import logging
import argparse
from pathlib import Path

from defs import LoggerError

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

# Device modules
import dev_keysightdaq973a
import dev_smchrs012
import dev_purpleair
import dev_kjlc354

logger = logging.getLogger()

CONFIGPATH_DEFAULT = 'config.ini'

def init_device(device):
    """
    Initialize the device and return an instance of the device class.

    device : dict
        Configuration dict of the device to initialize.
    """
    logger.info(
        'Trying to initialize device \'%s\' of model \'%s\'', device['Device'], device['Model'])

    device_instance = None

    # Keysight DAQ970A/973A multimeter (VISA)
    if device["Model"] == "Keysight DAQ973A":
        device_instance = dev_keysightdaq973a.Device(device)

    # SMC HRS012-AN-10-T chiller (RS-232)
    if device["Model"] == "SMC-HRS012-AN-10-T":
        device_instance = dev_smchrs012.Device(device)

    # PurpleAir air quality sensor/particle counters (web API)
    if device["Model"] == "PurpleAir":
        device_instance = dev_purpleair.Device(device)

    # Kurt J. Lesker KJLC 354 series ion pressure gauge (RS-485)
    if device["Model"] == "KJLC 354":
        device_instance = dev_kjlc354.Device(device)

    if device_instance is None:
        msg = f'Unknown device model \'{device["Model"]}\''
        logger.error(msg)
        raise LoggerError(msg)

    return device_instance

def _setup_logging():
    """Configure the application logging setup."""
    logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    _setup_logging()

    # Parse input arguments
    parser = argparse.ArgumentParser(
        description='logger2 (https://github.com/lmaisenbacher/logger2)')
    parser.add_argument(
        '-c','--config', dest='configpath', help='Path to configuration file', required=False,
        default=CONFIGPATH_DEFAULT)
    config_path = Path(parser.parse_args().configpath).absolute()

    # Read config file
    logger.info('Reading configuration from file \'%s\'', config_path)
    CONF = configparser.ConfigParser()
    files_read = CONF.read(config_path)
    if str(config_path) not in files_read:
        msg = f'Could not read configuration file \'{config_path}\''
        logger.error(msg)
        raise LoggerError(msg)

    DB_URL = CONF["Database"]["url"]
    DB_BUCKET = CONF["Database"]["bucket"]
    DB_ORG = CONF["Database"]["org"]
    DB_TOKEN = CONF["Database"]["token"]
    UPDATE_INTERVAL = int(CONF["Update"]["interval"])
    TIMEOUT = int(CONF["Devices"]["timeout"])

    device_config_path = Path(CONF["Devices"]["configpath"])
    # If path to `devices.json` is relative, use directory of `config.ini`
    if not device_config_path.is_absolute():
        device_config_path = config_path.parent.joinpath(device_config_path)

    def write_value(device, channel_id, value):
        """
        Write a new measured value to the InfluxDB database.

        device : dict
            Configuration dict of the device.
        channel_id : str
            ID of the measurement channel.
        value : float
            Measured value.
        """
        channel = device['Channels'][channel_id]
        tags = {
            'device': device['Device'],
            **device["tags"]
            }
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

    # Set up database connection
    client = influxdb_client.InfluxDBClient(
       url=DB_URL,
       token=DB_TOKEN,
       org=DB_ORG
    )
    write_api = client.write_api(write_options=SYNCHRONOUS)

    logger.info('Reading device configuration from file \'%s\'', device_config_path)
    try:
        with open(device_config_path) as device_config:
            devices = json.load(device_config)
    except FileNotFoundError as e:
        msg = f'Could not read device configuration file \'{device_config_path}\': {e}'
        logger.error(msg)
        raise LoggerError(msg)
    device_instances = []
    for i_device, device in enumerate(devices):
        device_instances.append(init_device(device))
    while True:

        try:

            for device, instance in zip(devices, device_instances):
                if device.get('Address') is not None:
                    logger.info(
                        'Reading device: \'%s\' at \'%s\'', device['Device'], device['Address'])
                else:
                    logger.info('Reading device: \'%s\'', device['Device'])
                if device["ParallelReadout"]:
                    try:
                        readings = instance.get_values()
                    except LoggerError as err:
                        logger.error('Could not get measurement values. Error: %s', err.value)
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

        except KeyboardInterrupt:
            break
