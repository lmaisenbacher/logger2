import sqlite3

conn = sqlite3.connect("new_database.sqlite", detect_types=sqlite3.PARSE_DECLTYPES)
c = conn.cursor()

c.execute('CREATE TABLE "Channels" ( `ChannelID` INTEGER, `DeviceID` INTEGER, `DeviceChannel` INTEGER, `Type` TEXT, `ShortName` TEXT, `LongName` TEXT, `Unit` TEXT, PRIMARY KEY(`ChannelID`) )')
c.execute('CREATE TABLE "Devices" ( `DeviceID` INTEGER, `Name` TEXT, `Address` TEXT, `Type` TEXT, `Model` TEXT, PRIMARY KEY(`DeviceID`) )')
c.execute('CREATE TABLE "Measurements" ( `MeasID` INTEGER, `DeviceID` INTEGER, `ChannelID` INTEGER, `Time` TIMESTAMP, `Value` NUMERIC, PRIMARY KEY(`MeasID`) )')
conn.commit()
c.close()
