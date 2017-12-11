import sqlite3
import configparser, json
import pfeiffer, vacom
import time
from serial import SerialException
import datetime

configpath = "config.ini"

conf = configparser.ConfigParser()
conf.read(configpath)

dbpath = conf["Database"]["path"]
updateInterval = int(conf["Update"]["interval"])

deviceConfigPath = conf["Devices"]["configpath"]

conn = sqlite3.connect(dbpath, detect_types=sqlite3.PARSE_DECLTYPES)
c = conn.cursor()

def writeValue(deviceID, channelID, time, value):
    """Write a new measured value to the database

    :param deviceID: ID of the measurement device
    :param channelID: ID of the measurement channel
    :param time: Timestamp as datetime object
    :param value: Measured value
    """
    c.execute("INSERT INTO Measurements (DeviceID, ChannelID, Time, Value) VALUES (?,?,?,?)", (deviceID, channelID, time, value))
    conn.commit()

def checkDeviceExists(name, address, deviceType, model):
    """Check if a device with specified name, address and type already exists in the database.

    :param name: Device name
    :param address: Device address
    :param deviceType: Device type
    :param model: Device model
    :returns: -1 if the device is not found in the database, the deviceID of the first matching device otherwhise
    """
    c.execute("SELECT * FROM Devices WHERE Name = ? AND Address = ? AND Type = ? AND Model = ?", (name, address, deviceType, model))
    res = c.fetchall()
    if(len(res)) > 0:
        return res[0][0]
    else:
        return -1

def addDevice(name, address, deviceType, model):
    """Add a new device to the database.

    :param name: Device name
    :param address: Device address
    :param deviceType: Device type
    :param model: Device model
    """
    c.execute("INSERT INTO Devices (Name, Address, Type, Model) VALUES (?,?,?,?)", (name, address, deviceType, model))
    conn.commit()

def checkChannelExists(deviceID, deviceChannel, measurementType, shortName, longName, unit):
    """Check if a measurement channel already exists in the database

    :param deviceID: ID of the device the channel belongs to
    :param deviceChannel: Channel measurement of the device
    :param measurementType: Which kind of measurement is taken
    :param shortName: Short description of the channel
    :param longName: Full description of the channel
    :param unit: Unit of the measurement
    :return: -1 if the channel is not found in the database, the channelID of the first matching channel otherwise
    """
    c.execute("SELECT * FROM Channels WHERE DeviceID = ? AND DeviceChannel = ? AND Type = ? AND ShortName = ? AND LongName = ? AND Unit = ?", (deviceID, deviceChannel, measurementType, shortName, longName, unit))
    res = c.fetchall()
    if(len(res)) > 0:
        return res[0][0]
    else:
        return -1

def addChannel(deviceID, deviceChannel, measurementType, shortName, longName, unit):
    """Add a new measurement channel to the database
    :param deviceID: ID of the device the channel belongs to
    :param deviceChannel: Channel measurement of the device
    :param measurementType: Which kind of measurement is taken
    :param shortName: Short description of the channel
    :param longName: Full description of the channel
    :param unit: Unit of the measurement
    """
    c.execute("INSERT INTO Channels (DeviceID, DeviceChannel, Type, ShortName, LongName, Unit) VALUES (?,?,?,?,?,?)",(deviceID, deviceChannel, measurementType, shortName, longName, unit))
    conn.commit()

def initDevices(devices):  
  
    for device in devices:
        print("Trying to open device {}".format(device["Name"]))
        if(device["Model"] == "Pfeiffer Vacuum TPG 261"):
            try:
                device["Object"] = pfeiffer.TPG261(device["Address"])
            except SerialException as err:
                print("Could not open serial device: {}".format(err))
                continue
        if(device["Model"] == "VACOM COLDION CU-100"):
            try:
                device["Object"] = vacom.CU100(device["Address"])
            except SerialException as err:
                print("Could not open serial device: {}".format(err))
                continue
        if(device["Model"] == "Keithley 2001 Multimeter"):
            device["Object"] = keithley.Multimeter(device["Address"])
            # Set up device parameters
            device["Object"].setNrMeasurements(device["NAverages"])
            device["Object"].setAveragingState(device["Averaging"])
            device["Object"].setNrPLCycles(device["NPLC"])

        deviceID = checkDeviceExists(device["Name"], device["Address"], device["Type"], device["Model"])
        if(deviceID == -1):
            addDevice(device["Name"], device["Address"], device["Type"], device["Model"])
            deviceID = checkDeviceExists(device["Name"], device["Address"], device["Type"], device["Model"])

        device["ID"] = deviceID
        for channel in device["Channels"]:
            channelID = checkChannelExists(deviceID, channel["DeviceChannel"], channel["Type"], channel["ShortName"], channel["LongName"], channel["Unit"])
            if(channelID == -1):
                addChannel(deviceID, channel["DeviceChannel"], channel["Type"], channel["ShortName"], channel["LongName"], channel["Unit"])
                channelID = checkChannelExists(deviceID, channel["DeviceChannel"], channel["Type"], channel["ShortName"], channel["LongName"], channel["Unit"])
            channel["ID"] = channelID

if __name__ == "__main__":
    with open(deviceConfigPath) as deviceConfig:
        devices = json.load(deviceConfig)
    initDevices(devices)
    while(True):
        for device in devices:
            print(device["Name"])
            for channel in device["Channels"]:
                print(channel["ShortName"])
                value = device["Object"].getValue(channel["DeviceChannel"])
                if("Multiplier" in channel):
                    value *= channel["Multiplier"]
                timestamp = datetime.datetime.now()
                print("{}\t{}".format(timestamp, value))
                writeValue(device["ID"], channel["ID"], timestamp, value)

        time.sleep(updateInterval)
