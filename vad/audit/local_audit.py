"""Local Audit Logger: Append-only dual-write logging to JSONL files and SQLite database."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str
    agent_id: str
    action: str
    tool: Optional[str] = None
    outcome: str
    eip_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LocalAuditLogger:
    """Thread-safe dual-write audit logger supporting JSONL streaming and SQLite structured queries."""

    def __init__(self, sqlite_path: str = "audit/audit.db", jsonl_path: str = "audit/audit.jsonl"):
        self.sqlite_path = sqlite_path
        self.jsonl_path = jsonl_path
        self._lock = threading.Lock()
        self._init_storage()

    def _init_storage(self) -> None:
        p_sql = Path(self.sqlite_path)
        p_sql.parent.mkdir(parents=True, exist_ok=True)
        p_jsonl = Path(self.jsonl_path)
        p_jsonl.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.sqlite_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    tool TEXT,
                    outcome TEXT NOT NULL,
                    eip_id TEXT,
                    metadata_json TEXT
                )
                """
            )
            conn.commit()

    def log(self, event: AuditEvent) -> None:
        with self._lock:
            # 1. Append to JSONL
            with open(self.jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.model_dump()) + "\n")

            # 2. Insert into SQLite
            with sqlite3.connect(self.sqlite_path) as conn:
                conn.execute(
                    """
                    INSERT INTO audit_events (
                        id, timestamp, session_id, agent_id, action, tool, outcome, eip_id, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.timestamp,
                        event.session_id,
                        event.agent_id,
                        event.action,
                        event.tool,
                        event.outcome,
                        event.eip_id,
                        json.dumps(event.metadata),
                    ),
                )
                conn.commit()

    def query_events(
        self,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEvent]:
        query = "SELECT id, timestamp, session_id, agent_id, action, tool, outcome, eip_id, metadata_json FROM audit_events WHERE 1=1"
        params: List[Any] = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if action:
            query += " AND action = ?"
            params.append(action)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        results = []
        with sqlite3.connect(self.sqlite_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor.fetchall():
                results.append(
                    AuditEvent(
                        id=row[0],
                        timestamp=row[1],
                        session_id=row[2],
                        agent_id=row[3],
                        action=row[4],
                        tool=row[5],
                        outcome=row[6],
                        eip_id=row[7],
                        metadata=json.loads(row[8]) if row[8] else {},
                    )
                )
        return results

    def count_events(self) -> int:
        with sqlite3.connect(self.sqlite_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM audit_events")
            return cursor.fetchone()[0]
