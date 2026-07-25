from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Union


class JsonlFormatter(logging.Formatter):
    _RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(
    path: Union[str, Path],
    level: int = logging.INFO,
    logger_name: str = "twinloop",
) -> logging.Logger:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(target, encoding="utf-8")
    handler.setFormatter(JsonlFormatter())

    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    logger.addHandler(handler)
    return logger
