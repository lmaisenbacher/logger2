"""Multi-purpose data logging software."""
import sqlite3
import configparser
import json
import datetime
import time
import logging

from serial import SerialException

import pfeiffer
import vacom
import keithley

LOG = logging.getLogger()

CONFIGPATH = "config.ini"

CONF = configparser.ConfigParser()
CONF.read(CONFIGPATH)

DBPATH = CONF["Database"]["path"]
UPDATE_INTERVAL = int(CONF["Update"]["interval"])

DEVICE_CONFIG_PATH = CONF["Devices"]["configpath"]

CONN = sqlite3.connect(DBPATH, detect_types=sqlite3.PARSE_DECLTYPES)
CURSOR = CONN.cursor()

def write_value(device_id, channel_id, timestamp, value):
    """Write a new measured value to the database

    :param device_id: ID of the measurement device
    :param channel_id: ID of the measurement channel
    :param timestamp: Timestamp as datetime object
    :param value: Measured value
    """
    CURSOR.execute("INSERT INTO Measurements (DeviceID, ChannelID, Time, Value) VALUES (?,?,?,?)",
                   (device_id, channel_id, timestamp, value))
    CONN.commit()

def check_device_exists(name, address, device_type, model):
    """Check if a device with specified name, address and type already exists in the database.

    :param name: Device name
    :param address: Device address
    :param device_type: Device type
    :param model: Device model
    :returns: -1 if the device is not found in the database, the device_id of the first matching
                 device otherwhise
    """
    CURSOR.execute(
        "SELECT * FROM Devices WHERE Name = ? AND Address = ? AND Type = ? AND Model = ?",
        (name, address, device_type, model))
    res = CURSOR.fetchall()
    if res:
        return res[0][0]
    return -1

def add_device(name, address, device_type, model):
    """Add a new device to the database.

    :param name: Device name
    :param address: Device address
    :param device_type: Device type
    :param model: Device model
    """
    CURSOR.execute("INSERT INTO Devices (Name, Address, Type, Model) VALUES (?,?,?,?)",
                   (name, address, device_type, model))
    CONN.commit()

def check_channel_exists(device_id, device_channel, measurement_type, short_name, long_name, unit):
    """Check if a measurement channel already exists in the database.

    :param device_id: ID of the device the channel belongs to
    :param device_channel: Channel measurement of the device
    :param measurement_type: Which kind of measurement is taken
    :param short_name: Short description of the channel
    :param long_name: Full description of the channel
    :param unit: Unit of the measurement
    :return: -1 if the channel is not found in the database, the channel_id of the first matching
             channel otherwise
    """
    CURSOR.execute("SELECT * FROM Channels WHERE DeviceID = ? AND DeviceChannel = ? AND Type = ?"
                   " AND ShortName = ? AND LongName = ? AND Unit = ?",
                   (device_id, device_channel, measurement_type, short_name, long_name, unit))
    res = CURSOR.fetchall()
    if res:
        return res[0][0]
    return -1

def add_channel(device_id, device_channel, measurement_type, short_name, long_name, unit):
    """Add a new measurement channel to the database.

    :param device_id: ID of the device the channel belongs to
    :param device_channel: Channel measurement of the device
    :param measurement_type: Which kind of measurement is taken
    :param short_name: Short description of the channel
    :param long_name: Full description of the channel
    :param unit: Unit of the measurement
    """
    CURSOR.execute("INSERT INTO Channels (DeviceID, DeviceChannel, Type, ShortName, LongName, Unit)"
                   " VALUES (?,?,?,?,?,?)",
                   (device_id, device_channel, measurement_type, short_name, long_name, unit))
    CONN.commit()

def init_devices(devices):
    """Initialize the device objects.

    :param devices: List of the devices to initialize
    """
    for device in devices:
        LOG.info("Trying to open device %s", device["Name"])
        if device["Model"] == "Pfeiffer Vacuum TPG 261":
            try:
                device["Object"] = pfeiffer.TPG261(device["Address"])
            except SerialException as err:
                LOG.error("Could not open serial device: %s", err)
                continue
        if device["Model"] == "Pfeiffer Vacuum TPG 262":
            try:
                device["Object"] = pfeiffer.TPG262(device["Address"])
            except SerialException as err:
                LOG.error("Could not open serial device: %s", err)
                continue
        if device["Model"] == "VACOM COLDION CU-100":
            try:
                device["Object"] = vacom.CU100(device["Address"])
            except SerialException as err:
                LOG.error("Could not open serial device: %s", err)
                continue
        if device["Model"] == "Keithley 2001 Multimeter":
            device["Object"] = keithley.Multimeter(device["Address"])
            # Set up device parameters
            device["Object"].setNrMeasurements(device["NAverages"])
            device["Object"].setAveragingState(device["Averaging"])
            device["Object"].setNrPLCycles(device["NPLC"])

        device_id = check_device_exists(device["Name"], device["Address"], device["Type"],
                                        device["Model"])
        if device_id == -1:
            add_device(device["Name"], device["Address"], device["Type"], device["Model"])
            device_id = check_device_exists(device["Name"], device["Address"], device["Type"],
                                            device["Model"])

        device["ID"] = device_id
        for channel in device["Channels"]:
            channel_id = check_channel_exists(
                device_id, channel["DeviceChannel"], channel["Type"], channel["ShortName"],
                channel["LongName"], channel["Unit"])
            if channel_id == -1:
                add_channel(device_id, channel["DeviceChannel"], channel["Type"],
                            channel["ShortName"], channel["LongName"], channel["Unit"])
                channel_id = check_channel_exists(device_id, channel["DeviceChannel"],
                                                  channel["Type"], channel["ShortName"],
                                                  channel["LongName"], channel["Unit"])
            channel["ID"] = channel_id

def _setup_logging():
    """Configure the application logging setup."""
    logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    _setup_logging()
    with open(DEVICE_CONFIG_PATH) as device_config:
        DEVICES = json.load(device_config)
    init_devices(DEVICES)
    while True:
        for current_device in DEVICES:
            LOG.info("%s", current_device["Name"])
            if current_device["ParallelReadout"]:
                readings = current_device["Object"].get_values()
                current_timestamp = datetime.datetime.now()
                for current_channel, reading in readings:
                    # This searches for a channel with matching DeviceChannel in the device
                    # config.
                    channel_information_dict = next((x for x in current_device["Channels"]
                                                     if x["DeviceChannel"] == current_channel),
                                                    None)
                    if channel_information_dict is None:
                        LOG.warning("Channel %s of device %s not configured.", current_channel,
                                    current_device["Name"])
                    else:
                        write_value(current_device["ID"], channel_information_dict["ID"],
                                    current_timestamp, reading)
            else:
                for current_channel in current_device["Channels"]:
                    LOG.info("%s", current_channel["ShortName"])
                    try:
                        measured_value = current_device["Object"].get_value(
                            current_channel["DeviceChannel"])
                    except (ValueError, IOError) as err:
                        LOG.error("Could not get measurement value. Error: %s", err)
                        continue
                    if "Multiplier" in current_channel:
                        measured_value *= current_channel["Multiplier"]
                    current_timestamp = datetime.datetime.now()
                    LOG.info("%s\t%s", current_timestamp, measured_value)
                    write_value(current_device["ID"], current_channel["ID"], current_timestamp,
                                measured_value)

        time.sleep(UPDATE_INTERVAL)
