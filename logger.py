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
from amodevices.dev_exceptions import DeviceError

import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS
from influxdb_client.client.exceptions import InfluxDBError

# Device modules
import dev_keysightdaq973a
import dev_smchrs012
import dev_purpleair
import dev_kjlc354
import dev_metonedr528
import dev_srsctc100
import dev_cryomechcpa1110
import dev_highfinesse
import dev_rp_lockbox
import dev_thorlabs_kpa101
import dev_thorlabs_mdt693b
import dev_thorlabs_pm100
import dev_slapdash

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

    # Keysight DAQ970A/973A multimeter (via VISA interface)
    if device['Model'] == 'Keysight DAQ973A':
        device_instance = dev_keysightdaq973a.Device(device)
    # SMC HRS012-AN-10-T chiller (via RS-232 port)
    if device['Model'] == 'SMC HRS012-AN-10-T':
        device_instance = dev_smchrs012.Device(device)
    # PurpleAir air quality sensor/particle counters (via web API)
    if device['Model'] == 'PurpleAir':
        device_instance = dev_purpleair.Device(device)
    # Kurt J. Lesker KJLC 354 series ion pressure gauge (via RS-485 port)
    if device['Model'] == 'KJLC 354':
        device_instance = dev_kjlc354.Device(device)
    # Met One DR-528 handheld particle counter (via RS-232 port)
    if device['Model'] == 'Met One DR-528':
        device_instance = dev_metonedr528.Device(device)
    # Stanford Research Instruments CTC100 cryogenic temperature controller
    # (via USB interface/virtual serial port)
    if device['Model'] == 'SRS CTC100':
        device_instance = dev_srsctc100.Device(device)
    # Cryomech CPA1110 helium compressor
    # (using Modbus TCP protocol over ethernet interface)
    if device['Model'] == 'Cryomech CPA1110':
        device_instance = dev_cryomechcpa1110.Device(device)
    # HighFinesse wavemeter
    # (using Windows DLL API)
    if device['Model'] == 'HighFinesse':
        device_instance = dev_highfinesse.Device(device)
    # Red Pitaya lockbox (rp-lockbox)
    if device['Model'] == 'rp-lockbox':
        device_instance = dev_rp_lockbox.Device(device)
    # Thorlabs KPA101 beam position aligner
    if device['Model'] == 'Thorlabs KPA101':
        device_instance = dev_thorlabs_kpa101.Device(device)
    # Thorlabs KPA101 beam position aligner
    if device['Model'] == 'Thorlabs MDT693B':
        device_instance = dev_thorlabs_mdt693b.Device(device)
    # Thorlabs PM100 power meter
    if device['Model'] == 'Thorlabs PM100':
        device_instance = dev_thorlabs_pm100.Device(device)
    # Slapdash server
    if device['Model'] == 'Slapdash':
        device_instance = dev_slapdash.Device(device)
    # Unknown device
    if device_instance is None:
        msg = f'Unknown device model \'{device["Model"]}\''
        logger.error(msg)
        raise LoggerError(msg)

    try:
        device_instance.connect()
    except (LoggerError, DeviceError) as err:
        logger.error('Could not connect. Error: %s', err.value)

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

    # Set up database connection
    client = influxdb_client.InfluxDBClient(
       url=DB_URL,
       token=DB_TOKEN,
       org=DB_ORG
    )
    write_api = client.write_api(write_options=SYNCHRONOUS)

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
            **device['tags']
            }
        tags.update(channel.get("tags", {}))
        if 'Multiplier' in channel:
            value *= channel['Multiplier']
        json_body = [
            {
                'measurement': device['measurement'],
                'fields': {channel['field-key']: value},
                'tags': tags
            }
        ]
        logger.info('Channel \'%s\': %s', channel_id, value)
        try:
            write_api.write(
                DB_BUCKET, DB_ORG, json_body)
        except InfluxDBError as e:
            logger.warning(f'Could not write to database: {e}')

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
                if device.get('ParallelReadout', True):
                    try:
                        readings = instance.get_values()
                    except (LoggerError, DeviceError) as err:
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
