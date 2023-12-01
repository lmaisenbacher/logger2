:: Initialize pipenv virtual environment. To find the path `<PATH_TO_VENV>`, use `pipenv --venv` in the directory of the virtual environment.
call <PATH_TO_VENV>\Scripts\activate.bat
:: Run the logger. Replace `<PATH_TO_LOGGER_REPO>` with the path to the directory of the `logger2` repo, and `<PATH_TO_CONFIG_FILE>` with the path the "config.ini" file.
cd <PATH_TO_LOGGER_REPO>
call python logger.py -c <PATH_TO_CONFIG_FILE>