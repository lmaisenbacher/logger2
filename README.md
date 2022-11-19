# logger2
A logger that reads out various devices and writes the output to an InfluxDB (>2.0).

Maintaned by Lothar Maisenbacher (UC Berkeley), partly based on earlier software from Fabian Schmid and me at Max Planck Institute of Quantum Optics (MPQ).

## Supported devices

- `Keysight DAQ973A`: Keysight DAQ970A/973A multimeter (via VISA interface)
- `SMC HRS012-AN-10-T`: SMC HRS012-AN-10-T chiller (via RS-232 port) (contributions by Jack Mango (UC Berkeley))
- `PurpleAir`: PurpleAir air quality sensor/particle counters (via web API)
- `KJLC 354`: Kurt J. Lesker KJLC 354 series ion pressure gauge (via RS-485 port) (contributed by Jack Mango (UC Berkeley))
- `Met One DR-528`: Met One DR-528 handheld particle counter (via RS-232 port) (contributed by Jack Mango (UC Berkeley))

## Preparation

### Installing `pipenv`

`pipenv` (https://pipenv.pypa.io/) is used to create a virtual environment and install the required packages for `logger2`. `pipenv` keeps track of all dependencies (i.e., required Python packages) in the file `Pipfile` in the repository.

The `pipenv` documentation recommends installing it as a user-package with `pip install --user pipenv`. When running `logger2` as a service/daemon, make sure that it is run under the user that `pipenv` was installed for, as it will not be found otherwise.

### Installing dependencies

Once `pipenv` is installed, the required dependencies of `logger2` can be installed in a virtual environment, which will be created if not already present, by navigating to the directory where `Pipfile` is located (here just the repository itself) and running

```
pipenv install
```

It might be desirable to use the option `--skip-lock` for the above command, which always uses `Pipfile` to resolve the requirements and not `Pipfile.lock`, and does not write an updated `Pipfile.lock`. This might be necessary when switching between different system architectures (e.g., Windows on AMD64 to Linux on Raspberry Pi).

To check whether the dependencies have been installed correctly, one can run Python in the newly created environment with

```
pipenv run python
```

and import the dependencies there.

On Raspberry Pi, importing `numpy` might not work right away, as compiled libraries are missing. Following https://github.com/numpy/numpy/issues/16012#issuecomment-615927988, these libraries can be installed with

```
sudo apt-get install libatlas-base-dev
```

### Adapting configuration

The logger uses two configuration files. `config.ini` contains the configuration of the database access, the update interval of the logger, and where the device configuration file is located. An example of a `config.ini` is included in the repository as `example_config.ini`. The device configuration file is a JSON files that lists which devices and which channels on the given devices are read by the logger. An example device configuration file is included as `example_devices.json`.

By default, the logger will look for `config.ini` in the current working directory. To use a specific `config.ini`, use the command line option `-c` to define the path to that file, e.g., `python logger.py -c /path/to/config.ini`.

## Running the logger

### Using `pipenv`

To run the logger in the newly created virtual environment, use

```
pipenv run python logger.py
```

The file `run.bat` in the repository just contains this line for convenience.

### As a service/daemon under Linux

The logger can be run as a system service, or daemon, in the background, also allowing it to be launched automatically after booting.

First, create a new daemon configuration file, here named `logger-chiller.service` to read out the temperatures of a laser chiller, using

```
sudo nano /etc/systemd/system/logger-chiller.service
```

Continuing the example of the laser chiller, the configuration file can e.g. look like

```
[Unit]
Description=Logger for chiller for 1064 nm fiber amplifier
After=multi-user.traget

[Service]
Type=simple
Restart=always
WorkingDirectory=/home/rp-chiller/Coding/logger2
ExecStart=/home/rp-chiller/.local/bin/pipenv run python /home/rp-chiller/Coding/logger2/logger.py
User=rp-chiller

[Install]
WantedBy=multi-user.target
```

Importantly, the daemon is run under the user `rp-chiller`, as given by `User=rp-chiller`, which is the user for which `pipenv` was installed here. Also important is to define the working directory with `WorkingDirectory=...` to be the directory where the virtual environment of `pipenv` was initialized, and where the configuration file `config.ini` and the device JSON file defined in that configuration file are located, which here is just the directory of the repository itself. To use a different `config.ini` - and the device JSON defined in that `config.ini` - use the `-c` command line argument, e.g., to use `home/rp-chiller/Coding/logger2-config/chiller/config.ini`:

```
ExecStart=/home/rp-chiller/.local/bin/pipenv run python /home/rp-chiller/Coding/logger2/logger.py -c /home/rp-chiller/Coding/logger2-config/chiller/config.ini
```

After creating a new configuration file or editing it, the configurations need to be re-loaded with

```
sudo systemctl daemon-reload
```

Now, we can start the daemon with

```
sudo systemctl start logger-chiller
```

The status of the daemon can be checked with

```
sudo systemctl status logger-chiller
```

If everything is fine, this should output something like

```
● logger-chiller.service - Logger for chiller for 1064 nm fiber amplifier
     Loaded: loaded (/etc/systemd/system/logger-chiller.service; enabled; vendor preset: enabled)
     Active: active (running) since Thu 2022-06-09 18:18:50 PDT; 3s ago
```

Note the `enabled` in the second line - this will only be there if the daemon has been enabled as described below.

The output of the daemon, which will also include more details in case it fails to start, can be viewed with

```
sudo journalctl -fu logger-chiller
```

To stop the service, use

```
sudo systemctl stop logger-chiller
```

If the daemon should be started upon boot, it needs to be 'enabled' with

```
sudo systemctl enable logger-chiller
```
