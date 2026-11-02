import json
import logging
import sys

from verdict.core.logs import JsonFormatter


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord("verdict.test", logging.INFO, __file__, 1, "claimed %s", (7,), None)
    record.__dict__.update(extra)
    return record


def test_record_is_formatted_as_one_json_object() -> None:
    line = JsonFormatter().format(make_record())
    entry = json.loads(line)
    assert entry["level"] == "INFO"
    assert entry["logger"] == "verdict.test"
    assert entry["message"] == "claimed 7"
    assert entry["time"].endswith("+00:00")
    assert "\n" not in line


def test_extra_fields_are_included_as_keys() -> None:
    entry = json.loads(JsonFormatter().format(make_record(submission_id=42, worker_id="w-1")))
    assert entry["submission_id"] == 42
    assert entry["worker_id"] == "w-1"


def test_exception_traceback_is_included() -> None:
    try:
        raise RuntimeError("sandbox exploded")
    except RuntimeError:
        record = make_record()
        record.exc_info = sys.exc_info()
    entry = json.loads(JsonFormatter().format(record))
    assert "RuntimeError: sandbox exploded" in entry["exception"]


def test_uvicorn_color_message_duplicate_is_dropped() -> None:
    entry = json.loads(JsonFormatter().format(make_record(color_message="\x1b[36m7\x1b[0m")))
    assert "color_message" not in entry
