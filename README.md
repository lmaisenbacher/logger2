# logger2
A logger that reads out various devices and writes the output to an InfluxDB (>2.0).

## Preparation

### Installing `pipenv`

`pipenv` (https://pipenv.pypa.io/) is used to create a virtual environment and install the required packages for `logger2`. `pipenv` keeps track of all dependencies (i.e., required Python packages) in the file `Pipfile` in the repository.

The `pipenv` documentation recommends installing it as a user-package with `pip install --user pipenv`. When running `logger2` as a service/daemon, make sure that it is run under the user that `pipenv` was installed for, as it will not be found otherwise.

### Installing dependencies

Once `pipenv` is installed, the required dependencies of `logger2` can be installed in a virtual environment, which will be created if not already present, with `pipenv install`.

## Running the logger

### Using `pipenv`

To run the logger in the newly created virtual environment, use

```
pipenv run python logger.py
```

The file `run.bat` in the repository just contains this line for convenience.

### As a service/daemon under Linux


