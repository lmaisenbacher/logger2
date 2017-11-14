import sqlite3
import configparser
import pfeiffer, vacom
import time
from serial import SerialException
import datetime

updateInteval = 1000 # Update interval in ms
dbpath = "database.sqlite"
configpath = "config.ini"

conn = sqlite3.connect(dbpath)

c = conn.cursor()

conf = configparser.ConfigParser()
conf.read(configpath)

devices = []

def writeValue(deviceID, deviceChannel, time, value, measurementType, name, longName, Unit):
    """Write a new measured value to the database

    :param deviceID: ID of the measurement device
    :param deviceChannel: Channel of the measurement device
    :param time: Timestamp in ISO format
    :param value: Measured value
    :param measurementType: Type of the measurement
    :param name: Short name of the measured value
    :param longName: Long name of the measured value
    :param unit: Unit of the measured value
    """
    c.execute("INSERT INTO Measurements (DeviceID, DeviceChannel, Time, Value, Type, Name, LongName, Unit) VALUES (?,?,?,?,?,?,?,?)", (deviceID, deviceChannel, time, value, measurementType, name, longName, unit))
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

def readConfig():
    for section in conf.sections():
        name = conf[section]["name"]
        address = conf[section]["address"]
        deviceType = conf[section]["type"]
        model = conf[section]["model"]

        if(model == "VACOM COLDION CU-100"):
            try:
                deviceObject = vacom.CU100(port=address)
            except SerialException as err:
                print("Could not open serial device: {}".format(err))
                continue
        elif(model == "Pfeiffer Vacuum TPG 261"):
            try:
                deviceObject = vacom.CU100(port=address)
            except SerialException as err:
                print("Could not open serial device: {}".format(err))
                continue

        deviceID = checkDeviceExists(name, address, deviceType, model)
        if(deviceID == -1):
            deviceID = addDevice(name, address, deviceType, model)

        devices.append({"object":deviceObject, "ID":deviceID})

if __name__ == "__main__":
    readConfig()
    while(True):
        for device in devices:
            value = device["object"].pressure_gauge()[0]
            timestamp = datetime.datetime.now().isoformat()
            writeValue(device["ID"], 1, timestamp, value, "TEMP", "Test", "Test", "mBar")

        time.sleep(updateInterval)
