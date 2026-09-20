"""Standard-Python-Logging je Sauna; Ausgabe über Home Assistants Loghandler."""

import logging

LEVELS = ("ERROR", "INFO", "DEBUG")


class SaunaLog:
    def __init__(self, identity, level="INFO"):
        self.logger = logging.getLogger(
            f"custom_components.ha_sauna.instance.{identity}"
        )
        self.set_level(level)
        self.previous = {}

    def set_level(self, level):
        if level not in LEVELS:
            raise ValueError("Protokollstufe muss ERROR, INFO oder DEBUG sein.")
        self.logger.setLevel(level)

    def change(self, key, value, level, message, *args):
        """Nur Änderungen melden; wiederholte Sekundenprüfungen bleiben still."""
        if key in self.previous and self.previous[key] == value:
            return
        self.previous[key] = value
        self.logger.log(level, message, *args, extra={"sauna_event": key})

    def info(self, event, message, *args):
        self.logger.info(message, *args, extra={"sauna_event": event})

    def debug(self, event, message, *args):
        self.logger.debug(message, *args, extra={"sauna_event": event})

    def error(self, event, message, *args):
        self.logger.error(message, *args, extra={"sauna_event": event})
