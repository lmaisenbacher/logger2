import logging
import time

import dev_kjlcxcg

logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)

device = {
    "Device": "Pressure Gauge Controller",
    "tags": {},
    "measurement": "pressure",
    "Timeout": 1,
    "Channels":
        {
            "Channel1":
            {
                "Type": "Pressure",
                "DeviceChannel": 0,
                "field-key": "Pressure",
                "tags":
                {
                    "sensor": "Pressure",
                    "unit": "Torr"
                }
            },
            "Channel2":
            {
                "Type": "Pressure",
                "DeviceChannel": 1,
                "field-key": "Pressure",
                "tags":
                {
                    "sensor": "Pressure",
                    "unit": "Torr"
                }
            }
    }
}
device_instance = dev_kjlcxcg.Device(device)
device_instance.connect()

# Test reading and display

while True:
    readings = device_instance.get_values()
    logger.info(readings)
    time.sleep(device["Timeout"])
