"""Storage module providing SQLite persistence using standard library sqlite3.

This is the ONLY module in the API service that interacts with the SQLite database.
All queries are parameterized to prevent SQL injection. Foreign keys and WAL mode are enabled.
"""

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional


def get_connection(db_path: str) -> sqlite3.Connection:
    """Create and return a configured sqlite3 connection with WAL mode and foreign keys."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for high concurrency between reader/writer processes
    conn.execute("PRAGMA journal_mode = WAL;")
    # Enforce relational foreign key constraints
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str) -> None:
    """Initialize database tables and indexes if they do not exist."""
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                voice TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS calls (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES agents(id),
                room_name TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK(status IN ('created', 'active', 'ended', 'failed')),
                created_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                compliance TEXT
            );

            CREATE TABLE IF NOT EXISTS transcript_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                speaker TEXT NOT NULL CHECK(speaker IN ('agent', 'user')),
                text TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                UNIQUE(call_id, seq)
            );

            CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);
            CREATE INDEX IF NOT EXISTS idx_transcript_call_seq ON transcript_lines(call_id, seq);
            """
        )

        # Ensure compliance column exists on existing installations
        cursor = conn.execute("PRAGMA table_info(calls);")
        columns = [row["name"] for row in cursor.fetchall()]
        if "compliance" not in columns:
            conn.execute("ALTER TABLE calls ADD COLUMN compliance TEXT;")


# --- Agent Storage Functions ---

def create_agent(
    db_path: str,
    agent_id: str,
    name: str,
    system_prompt: str,
    voice: str,
    model: str,
    created_at: str,
) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO agents (id, name, system_prompt, voice, model, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (agent_id, name, system_prompt, voice, model, created_at),
        )
    return {
        "id": agent_id,
        "name": name,
        "system_prompt": system_prompt,
        "voice": voice,
        "model": model,
        "created_at": created_at,
    }


def get_agent(db_path: str, agent_id: str) -> Optional[dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT id, name, system_prompt, voice, model, created_at FROM agents WHERE id = ?;",
            (agent_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def list_agents(db_path: str) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT id, name, system_prompt, voice, model, created_at FROM agents ORDER BY created_at DESC;"
        )
        return [dict(row) for row in cursor.fetchall()]


# --- Call Storage Functions ---

def create_call(
    db_path: str,
    call_id: str,
    agent_id: str,
    room_name: str,
    created_at: str,
) -> dict[str, Any]:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO calls (id, agent_id, room_name, status, created_at)
            VALUES (?, ?, ?, 'created', ?);
            """,
            (call_id, agent_id, room_name, created_at),
        )
    return {
        "id": call_id,
        "agent_id": agent_id,
        "room_name": room_name,
        "status": "created",
        "created_at": created_at,
        "started_at": None,
        "ended_at": None,
    }


def get_call(db_path: str, call_id: str) -> Optional[dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT id, agent_id, room_name, status, created_at, started_at, ended_at, compliance FROM calls WHERE id = ?;",
            (call_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def update_agent_system_prompt(db_path: str, agent_id: str, system_prompt: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE agents SET system_prompt = ? WHERE id = ?;",
            (system_prompt, agent_id),
        )


def list_calls(db_path: str, status: Optional[str] = None) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        if status:
            cursor = conn.execute(
                """
                SELECT c.id, c.agent_id, a.name AS agent_name, c.room_name, c.status, c.created_at, c.started_at, c.ended_at, c.compliance
                FROM calls c
                LEFT JOIN agents a ON c.agent_id = a.id
                WHERE c.status = ?
                ORDER BY c.created_at DESC;
                """,
                (status,),
            )
        else:
            cursor = conn.execute(
                """
                SELECT c.id, c.agent_id, a.name AS agent_name, c.room_name, c.status, c.created_at, c.started_at, c.ended_at, c.compliance
                FROM calls c
                LEFT JOIN agents a ON c.agent_id = a.id
                ORDER BY c.created_at DESC;
                """
            )
        return [dict(row) for row in cursor.fetchall()]


def mark_call_failed(db_path: str, call_id: str) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE calls SET status = 'failed' WHERE id = ?;",
            (call_id,),
        )


def set_call_started(db_path: str, call_id: str, started_at: str) -> Optional[dict[str, Any]]:
    """Idempotently set call to active status and record started_at."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE calls
            SET status = 'active',
                started_at = COALESCE(started_at, ?)
            WHERE id = ? AND status IN ('created', 'active');
            """,
            (started_at, call_id),
        )
    return get_call(db_path, call_id)


def transition_call_to_ended(
    db_path: str, call_id: str, ended_at: str, initial_compliance_json: Optional[str] = None
) -> tuple[Optional[dict[str, Any]], bool]:
    """Atomically transitions call to 'ended' if not already ended.

    Returns (call_dict, was_transitioned).
    was_transitioned is True if and only if cursor.rowcount == 1, ensuring idempotent scheduling.
    """
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE calls
            SET status = 'ended',
                ended_at = COALESCE(ended_at, ?),
                compliance = COALESCE(compliance, ?)
            WHERE id = ? AND status != 'ended';
            """,
            (ended_at, initial_compliance_json, call_id),
        )
        was_transitioned = (cursor.rowcount == 1)
    call = get_call(db_path, call_id)
    return call, was_transitioned


def set_call_ended(db_path: str, call_id: str, ended_at: str) -> Optional[dict[str, Any]]:
    """Idempotently set call to ended status and record ended_at."""
    call, _ = transition_call_to_ended(db_path, call_id, ended_at)
    return call


def update_call_compliance(db_path: str, call_id: str, compliance_data: dict[str, Any]) -> None:
    """Persist the evaluated compliance result JSON to calls.compliance."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE calls SET compliance = ? WHERE id = ?;",
            (json.dumps(compliance_data), call_id),
        )


# --- Transcript Storage Functions ---

def upsert_transcript_lines(db_path: str, call_id: str, lines: list[dict[str, Any]]) -> None:
    """Upsert transcript lines idempotently by (call_id, seq)."""
    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO transcript_lines (call_id, seq, speaker, text, started_at, ended_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(call_id, seq) DO UPDATE SET
                speaker = excluded.speaker,
                text = excluded.text,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at;
            """,
            [
                (
                    call_id,
                    line["seq"],
                    line["speaker"],
                    line["text"],
                    line["started_at"],
                    line["ended_at"],
                )
                for line in lines
            ],
        )


def clamp_agent_transcript_lines(db_path: str, call_id: str, call_ended_at: str) -> None:
    """Clamps any agent transcript line ended_at to the call ended_at in SQLite."""
    try:
        call_ended_dt = datetime.fromisoformat(call_ended_at)
    except Exception:
        return

    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT seq, ended_at FROM transcript_lines WHERE call_id = ? AND speaker = 'agent';",
            (call_id,),
        )
        for row in cursor.fetchall():
            try:
                line_ended_dt = datetime.fromisoformat(row["ended_at"])
                if line_ended_dt > call_ended_dt:
                    conn.execute(
                        "UPDATE transcript_lines SET ended_at = ? WHERE call_id = ? AND seq = ?;",
                        (call_ended_at, call_id, row["seq"]),
                    )
            except Exception:
                pass


def get_transcript_lines(db_path: str, call_id: str, clamp_to_call_ended: bool = True) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT seq, speaker, text, started_at, ended_at
            FROM transcript_lines
            WHERE call_id = ?
            ORDER BY seq ASC;
            """,
            (call_id,),
        )
        lines = [dict(row) for row in cursor.fetchall()]

    if clamp_to_call_ended and lines:
        call = get_call(db_path, call_id)
        if call and call.get("ended_at"):
            call_ended_iso = call["ended_at"]
            try:
                call_ended_dt = datetime.fromisoformat(call_ended_iso)
                for line in lines:
                    if line["speaker"] == "agent" and line.get("ended_at"):
                        line_ended_dt = datetime.fromisoformat(line["ended_at"])
                        if line_ended_dt > call_ended_dt:
                            line["ended_at"] = call_ended_iso
            except Exception:
                pass

    return lines
