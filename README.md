# logger2
A logger that reads out various devices and writes the output to an InfluxDB (>2.0).

## Preparation

### Installing `pipenv`

`pipenv` (https://pipenv.pypa.io/) is used to create a virtual environment and install the required packages for `logger2`. `pipenv` keeps track of all dependencies (i.e., required Python packages) in the file `Pipfile` in the repository.

The `pipenv` documentation recommends installing it as a user-package with `pip install --user pipenv`. When running `logger2` as a service/daemon, make sure that it is run under the user that `pipenv` was installed for, as it will not be found otherwise.

### Installing dependencies

Once `pipenv` is installed, the required dependencies of `logger2` can be installed in a virtual environment, which will be created if not already present, by navigating to the directory where `Pipfile` is located (here just the repository itself) and running

```
pipenv install
```

To check whether the dependencies have been installed correctly, one can run Python in the newly created environment with

```
pipenv run python
```

and import the dependencies there.

On Raspberry Pi, importing `numpy` might not work right away, as compiled libraries are missing. Following https://github.com/numpy/numpy/issues/16012#issuecomment-615927988, these libraries can be installed with

```
sudo apt-get install libatlas-base-dev
```

## Running the logger

### Using `pipenv`

To run the logger in the newly created virtual environment, use

```
pipenv run python logger.py
```

The file `run.bat` in the repository just contains this line for convenience.

### As a service/daemon under Linux

The logger can be run as a system service, or daemon, in the background, also allowing it to be launched automatically after booting.

First, create a new daemon configuration file, here named `logger_chiller.service` to read out the temperatures of a laser chiller, using

```
sudo nano /etc/systemd/system/logger_chiller.service
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

Importantly, the daemon is run under the user `rp-chiller`, as given by `User=rp-chiller`, which is the user for which `pipenv` was installed here. Also important is to define the working directory with `WorkingDirectory=...` to be the directory where the configuration file `config.ini` and the device JSON file defined in that configuration file are located, which here is just the directory of the repository itself.

After creating a new configuration file or editing it, the configurations need to be re-loaded with

```
sudo systemctl daemon-reload
```

Now, we can start the daemon with

```
sudo systemctl start logger_chiller
```

The status of the daemon can be checked with

```
sudo systemctl status logger_chiller
```

The output of the daemon, which will also include more details in case it fails to start, can be viewed with

```
sudo journalctl -fu logger_chiller
```

If the daemon should be started upon boot, it needs to be 'enabled' with

```
sudo systemctl enable logger_chiller
```