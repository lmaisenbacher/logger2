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

from defs import LoggerError

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS

# Device modules
import dev_keysightdaq973a
import dev_smc
import dev_purpleair

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
    
    if device["Model"] == "Keysight DAQ973A":
        device_instance = dev_keysightdaq973a.Device(device)
        
    if device["Model"] == "SMC-HRS012-AN-10-T":
        device_instance = dev_smc.Device(device)
        
    if device["Model"] == "PurpleAir":
        device_instance = dev_purpleair.Device(device)        

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
    parser = argparse.ArgumentParser(description='Log measurement groups.')
    parser.add_argument(
        '-c','--config', dest='configpath', help='Measurement groups', required=False,
        default=CONFIGPATH_DEFAULT)
    CONFIGPATH = parser.parse_args().configpath
    
    # Read config file
    logger.info('Reading config from file \'%s\'', CONFIGPATH)
    CONF = configparser.ConfigParser()
    files_read = CONF.read(CONFIGPATH)
    if CONFIGPATH not in files_read:
        msg = f'Could not read configuration file \'{CONFIGPATH}\''
        logger.error(msg)
        raise LoggerError(msg)        
    
    DB_URL = CONF["Database"]["url"]
    DB_BUCKET = CONF["Database"]["bucket"]
    DB_ORG = CONF["Database"]["org"]
    DB_TOKEN = CONF["Database"]["token"]
    UPDATE_INTERVAL = int(CONF["Update"]["interval"])
    TIMEOUT = int(CONF["Devices"]["timeout"])
    
    DEVICE_CONFIG_PATH = CONF["Devices"]["configpath"]
    
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
    
    with open(DEVICE_CONFIG_PATH) as device_config:
        devices = json.load(device_config)
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
