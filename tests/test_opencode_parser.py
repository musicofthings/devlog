import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from devlog.sources.opencode import OpenCodeParser

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "opencode"


def _write_opencode_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT,
            title TEXT,
            time_created INTEGER,
            time_updated INTEGER
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            time_created INTEGER,
            data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            session_id TEXT,
            time_created INTEGER,
            data TEXT
        );
        """
    )
    created = int(datetime(2026, 7, 20, 12, 0, tzinfo=UTC).timestamp() * 1000)
    cur.execute(
        "INSERT INTO session (id, directory, title, time_created, time_updated) VALUES (?,?,?,?,?)",
        ("ses_sample", "/Users/dev/code/helios", "Helios session", created, created + 60_000),
    )
    cur.execute(
        "INSERT INTO message (id, session_id, time_created, data) VALUES (?,?,?,?)",
        (
            "msg_user",
            "ses_sample",
            created,
            json.dumps({"role": "user"}),
        ),
    )
    cur.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data) VALUES (?,?,?,?,?)",
        (
            "part_text",
            "msg_user",
            "ses_sample",
            created,
            json.dumps({"type": "text", "text": "Implement the OpenCode source parser"}),
        ),
    )
    cur.execute(
        "INSERT INTO message (id, session_id, time_created, data) VALUES (?,?,?,?)",
        (
            "msg_asst",
            "ses_sample",
            created + 5_000,
            json.dumps({"role": "assistant"}),
        ),
    )
    cur.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data) VALUES (?,?,?,?,?)",
        (
            "part_tool",
            "msg_asst",
            "ses_sample",
            created + 5_000,
            json.dumps(
                {
                    "type": "tool",
                    "tool": "bash",
                    "state": {"input": {"command": "pytest -q", "path": "/Users/dev/code/helios"}},
                }
            ),
        ),
    )
    con.commit()
    con.close()


def test_sample_sqlite_fixture():
    sessions = OpenCodeParser().iter_sessions(FIXTURES)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "opencode"
    assert s.session_id == "ses_sample"
    assert "helios" in s.project_path.replace("\\", "/")
    assert any(e.user_message and "OpenCode source parser" in e.user_message for e in s.events)
    assert any(e.tool_name == "bash" for e in s.events)
    assert any(e.bash_command and "pytest" in e.bash_command for e in s.events)


def test_tmp_sqlite_and_malformed_parts(tmp_path: Path):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data) VALUES (?,?,?,?,?)",
        ("bad", "msg_user", "ses_sample", 0, "NOT JSON"),
    )
    con.commit()
    con.close()
    sessions = OpenCodeParser().iter_sessions(tmp_path)
    assert len(sessions) == 1
    assert any(e.user_message for e in sessions[0].events)


def test_legacy_json_storage(tmp_path: Path):
    path = tmp_path / "storage" / "session" / "ses_legacy.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "id": "ses_legacy",
                "directory": "/tmp/legacy",
                "time": {"created": 1753005600000},
                "messages": [
                    {
                        "role": "user",
                        "time": {"created": 1753005600000},
                        "content": "legacy json session",
                    },
                    {
                        "role": "assistant",
                        "parts": [
                            {
                                "type": "tool",
                                "tool": "edit",
                                "state": {"input": {"path": "/tmp/legacy/a.py"}},
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    sessions = OpenCodeParser().iter_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0].session_id == "ses_legacy"
    assert sessions[0].project_path.replace("\\", "/") == "/tmp/legacy"
    assert any(e.user_message == "legacy json session" for e in sessions[0].events)
    assert any(e.tool_name == "edit" for e in sessions[0].events)


def test_missing_db_returns_empty(tmp_path: Path):
    assert OpenCodeParser().iter_sessions(tmp_path) == []
