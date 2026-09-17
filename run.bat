:: Run the logger in the repository's uv environment (create it once with `uv sync` in the repository directory).
:: Replace <PATH_TO_LOGGER_REPO> with the path of the logger2 repository and <PATH_TO_CONFIG_FILE> with the path of the "config.ini" to use.
cd /d <PATH_TO_LOGGER_REPO>
uv run logger.py -c <PATH_TO_CONFIG_FILE>
