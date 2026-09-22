import json
import logging


class SafeJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("correlation_id", "request_id", "error_type", "blob_key"):
            if hasattr(record, name):
                fields[name] = str(getattr(record, name))
        return json.dumps(fields)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())
    logging.getLogger("gametheory").handlers = [handler]
    logging.getLogger("gametheory").setLevel(logging.INFO)
    logging.getLogger("gametheory").propagate = False
