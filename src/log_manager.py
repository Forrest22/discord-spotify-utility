""" log management and setup module """
import logging
from os import getenv

LOGGER_COLORS = {
    "discord-spotify-util.spotify": "\033[32m",  # green
    "discord-spotify-util.discord": "\033[36m",  # cyan
    "discord-spotify-util.db":      "\033[34m",  # blue
    "discord.client":               "\033[35m",  # purple
    "discord.gateway":              "\033[35m",  # purple
    "discord.http":                 "\033[35m",  # purple
}

LEVEL_COLORS = {
    "WARNING":  "\033[33m",  # yellow
    "ERROR":    "\033[31m",  # red
    "CRITICAL": "\033[1;31m",  # bold red
}

RESET = "\033[0m"


class ModuleColorFormatter(logging.Formatter):
    """ModuleColorFormatter
    A quick implementation of color coding different modules

    Args:
        logging (_type_): _description_
    """
    def format(self, record):
        message = super().format(record)
        if record.levelno >= logging.WARNING:
            color = LEVEL_COLORS.get(record.levelname, "")
        else:
            color = LOGGER_COLORS.get(record.name, "")
        return f"{color}{message}{RESET}" if color else message


def setup_logging(
    log_file: str = "discord-spotify-util.log",
) -> None:
    """Logging Setup

    Args:
        log_file (str, optional): Name of log file. Defaults to "discord-spotify-util.log".
    """
    log_format = getenv("LOGGING_FORMAT")
    date_format = getenv("DATE_FORMAT")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    file_formatter = logging.Formatter(fmt=log_format, datefmt=date_format)
    console_formatter = ModuleColorFormatter(fmt=log_format, datefmt=date_format)

    # File handler
    log_file_handler = logging.FileHandler(
        filename=log_file, encoding="utf-8", mode="w"
    )
    log_file_handler.setFormatter(file_formatter)

    # Console handler
    log_console_handler = logging.StreamHandler()
    log_console_handler.setFormatter(console_formatter)

    root_logger.addHandler(log_file_handler)
    root_logger.addHandler(log_console_handler)
