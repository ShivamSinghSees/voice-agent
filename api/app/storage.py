"""Storage module providing SQLite persistence using standard library sqlite3.

This is the ONLY module in the API service that interacts with the SQLite database.
All queries are parameterized to prevent SQL injection. Foreign keys and WAL mode are enabled.
"""

import sqlite3
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
                ended_at TEXT
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
            "SELECT id, agent_id, room_name, status, created_at, started_at, ended_at FROM calls WHERE id = ?;",
            (call_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def list_calls(db_path: str, status: Optional[str] = None) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        if status:
            cursor = conn.execute(
                "SELECT id, agent_id, room_name, status, created_at, started_at, ended_at FROM calls WHERE status = ? ORDER BY created_at DESC;",
                (status,),
            )
        else:
            cursor = conn.execute(
                "SELECT id, agent_id, room_name, status, created_at, started_at, ended_at FROM calls ORDER BY created_at DESC;"
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


def set_call_ended(db_path: str, call_id: str, ended_at: str) -> Optional[dict[str, Any]]:
    """Idempotently set call to ended status and record ended_at.

    NOTE: This is the single central function that transitions a call to 'ended'.
    Phase 6 post-call compliance verification will hook in directly after this transition.
    """
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE calls
            SET status = 'ended',
                ended_at = COALESCE(ended_at, ?)
            WHERE id = ?;
            """,
            (ended_at, call_id),
        )
    return get_call(db_path, call_id)


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


def get_transcript_lines(db_path: str, call_id: str) -> list[dict[str, Any]]:
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
        return [dict(row) for row in cursor.fetchall()]
