from pathlib import Path

from devlog.status import load_status, record_event, status_path


def test_load_status_missing_file_returns_empty_dict(tmp_path: Path):
    assert load_status(tmp_path) == {}


def test_load_status_corrupt_file_returns_empty_dict(tmp_path: Path):
    status_path(tmp_path).write_text("{not valid json", encoding="utf-8")

    assert load_status(tmp_path) == {}


def test_record_event_writes_and_round_trips(tmp_path: Path):
    path = record_event(
        tmp_path, event="published", date="2026-08-06", at="2026-08-07T06:30:00+00:00"
    )

    assert path == status_path(tmp_path)
    data = load_status(tmp_path)
    assert data["last_published_date"] == "2026-08-06"
    assert data["last_published_at"] == "2026-08-07T06:30:00+00:00"


def test_record_event_preserves_other_event_keys(tmp_path: Path):
    record_event(tmp_path, event="published", date="2026-08-06", at="2026-08-07T06:30:00+00:00")
    record_event(tmp_path, event="deleted", date="2026-07-19", at="2026-08-07T07:03:15+00:00")

    data = load_status(tmp_path)
    assert data["last_published_date"] == "2026-08-06"
    assert data["last_deleted_date"] == "2026-07-19"
    assert data["last_deleted_at"] == "2026-08-07T07:03:15+00:00"


def test_record_event_defaults_at_to_now_when_omitted(tmp_path: Path):
    record_event(tmp_path, event="published", date="2026-08-06")

    data = load_status(tmp_path)
    assert "last_published_at" in data
    assert data["last_published_at"]
