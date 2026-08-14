import json
import sqlite3
from pathlib import Path

from devlog.sources.warp import WarpParser

FIXTURES = Path(__file__).resolve().parents[1] / "sample_data" / "warp"


def _write_warp_db(path: Path, *, with_row: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE agent_conversations (
            id INTEGER PRIMARY KEY,
            conversation_id TEXT,
            conversation_data TEXT,
            last_modified_at TIMESTAMP,
            summary TEXT
        );
        CREATE TABLE ai_queries (
            id INTEGER PRIMARY KEY,
            exchange_id TEXT,
            conversation_id TEXT,
            start_ts DATETIME,
            input TEXT,
            working_directory TEXT,
            output_status TEXT,
            model_id TEXT,
            planning_model_id TEXT,
            coding_model_id TEXT
        );
        CREATE TABLE agent_tasks (
            id INTEGER PRIMARY KEY,
            conversation_id TEXT,
            task_id TEXT,
            task BLOB,
            last_modified_at TIMESTAMP
        );
        """
    )
    if with_row:
        conv_data = json.dumps(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "name": "run_command",
                                "input": {"command": "cargo test", "path": "src/main.rs"},
                            }
                        ],
                    }
                ]
            }
        )
        cur.execute(
            "INSERT INTO agent_conversations "
            "(conversation_id, conversation_data, last_modified_at, summary) "
            "VALUES (?,?,?,?)",
            ("conv-1", conv_data, "2026-07-20 10:30:00", "Warp agent session"),
        )
        cur.execute(
            "INSERT INTO ai_queries "
            "(exchange_id, conversation_id, start_ts, input, working_directory, output_status) "
            "VALUES (?,?,?,?,?,?)",
            (
                "ex-1",
                "conv-1",
                "2026-07-20T10:00:00Z",
                "Fix the Warp agent transcript parser",
                "C:/Users/dev/code/devlog",
                "ok",
            ),
        )
    con.commit()
    con.close()


def test_sample_sqlite_fixture():
    sessions = WarpParser().iter_sessions(FIXTURES)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.source == "warp"
    assert s.session_id == "conv-1"
    assert "devlog" in s.project_path.replace("\\", "/")
    assert any(e.user_message and "Warp agent" in e.user_message for e in s.events)
    assert any(e.tool_name == "run_command" for e in s.events)
    assert any(e.bash_command and "cargo test" in e.bash_command for e in s.events)


def test_empty_tables_return_no_sessions(tmp_path: Path):
    _write_warp_db(tmp_path / "data" / "warp.sqlite", with_row=False)
    assert WarpParser().iter_sessions(tmp_path) == []


def test_missing_db_returns_empty(tmp_path: Path):
    assert WarpParser().iter_sessions(tmp_path) == []


def test_populated_tmp_db(tmp_path: Path):
    _write_warp_db(tmp_path / "data" / "warp.sqlite", with_row=True)
    sessions = WarpParser().iter_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0].source == "warp"
