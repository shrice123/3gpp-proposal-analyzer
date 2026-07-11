from __future__ import annotations

import json
import sqlite3
import threading
import queue
import time
import os
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import SCHEMA_VERSION, settings
from .source_normalization import apply_meeting_parent_names, alias_key, canonical_source_name, extract_primary_source


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  index_file TEXT,
  proposal_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
  tdoc TEXT NOT NULL,
  title TEXT NOT NULL,
  source TEXT NOT NULL,
  primary_source TEXT NOT NULL DEFAULT 'Unknown',
  canonical_source TEXT NOT NULL DEFAULT 'Unknown',
  agenda_item TEXT NOT NULL,
  agenda_description TEXT NOT NULL,
  agenda_sort REAL NOT NULL DEFAULT 999999,
  status TEXT NOT NULL DEFAULT '',
  abstract TEXT NOT NULL DEFAULT '',
  zip_url TEXT NOT NULL,
  file_url TEXT NOT NULL DEFAULT '',
  file_name TEXT NOT NULL DEFAULT '',
  file_kind TEXT NOT NULL DEFAULT 'zip',
  file_size INTEGER,
  file_modified TEXT,
  metadata_source TEXT NOT NULL DEFAULT 'index',
  analyzable INTEGER NOT NULL DEFAULT 1,
  download_state TEXT NOT NULL DEFAULT 'idle',
  analysis_state TEXT NOT NULL DEFAULT 'idle',
  preparation_state TEXT NOT NULL DEFAULT 'idle',
  preparation_error TEXT,
  UNIQUE(meeting_id, tdoc)
);
CREATE INDEX IF NOT EXISTS idx_proposals_meeting ON proposals(meeting_id);
CREATE INDEX IF NOT EXISTS idx_proposals_agenda ON proposals(meeting_id, agenda_item);
CREATE INDEX IF NOT EXISTS idx_proposals_source ON proposals(meeting_id, source);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  progress REAL NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL,
  result_json TEXT,
  partial_result_json TEXT,
  thread_id TEXT,
  client_request_id TEXT,
  heartbeat_at TEXT,
  stage TEXT NOT NULL DEFAULT 'queued',
  deadline_at TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  data_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id,id);
CREATE TABLE IF NOT EXISTS job_items (
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  proposal_id TEXT NOT NULL,
  tdoc TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  heartbeat_at TEXT NOT NULL,
  deadline_at TEXT,
  error TEXT,
  PRIMARY KEY(job_id,proposal_id,stage)
);
CREATE TABLE IF NOT EXISTS analyses (
  proposal_id TEXT PRIMARY KEY REFERENCES proposals(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  summary_json TEXT NOT NULL,
  citations_json TEXT NOT NULL,
  visual_count INTEGER NOT NULL DEFAULT 0,
  vision_complete INTEGER NOT NULL DEFAULT 0,
  visual_coverage_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preparations (
  proposal_id TEXT PRIMARY KEY REFERENCES proposals(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  documents_json TEXT NOT NULL,
  citations_json TEXT NOT NULL,
  visual_files_json TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  visual_count INTEGER NOT NULL DEFAULT 0,
  visual_evidence_json TEXT NOT NULL DEFAULT '[]',
  resolved_visual_count INTEGER NOT NULL DEFAULT 0,
  unresolved_visual_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visual_analysis_cache (
  asset_sha256 TEXT NOT NULL,
  model_key TEXT NOT NULL,
  result TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(asset_sha256, model_key)
);
CREATE TABLE IF NOT EXISTS analysis_summaries (
  proposal_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
  model_option_id TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  summary_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(proposal_id,model_option_id,source_sha256,prompt_version)
);
CREATE VIRTUAL TABLE IF NOT EXISTS analysis_fts USING fts5(
  proposal_id UNINDEXED,
  tdoc,
  title,
  content,
  tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS model_profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  base_url TEXT NOT NULL,
  text_model TEXT NOT NULL DEFAULT '',
  vision_model TEXT NOT NULL DEFAULT '',
  embedding_model TEXT NOT NULL DEFAULT '',
  api_key_encrypted TEXT,
  available_models_json TEXT NOT NULL DEFAULT '[]',
  deployment TEXT NOT NULL DEFAULT 'external',
  max_output_tokens INTEGER NOT NULL DEFAULT 8192,
  context_window INTEGER NOT NULL DEFAULT 65536,
  max_concurrency INTEGER NOT NULL DEFAULT 4,
  request_timeout_seconds INTEGER NOT NULL DEFAULT 180,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_test_ok INTEGER NOT NULL DEFAULT 0,
  last_tested_at TEXT,
  is_default INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_options (
  id TEXT PRIMARY KEY,
  normalized_name TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  profile_id TEXT NOT NULL REFERENCES model_profiles(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_threads (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  proposal_ids_json TEXT NOT NULL,
  title TEXT NOT NULL,
  default_model_option_id TEXT,
  summary TEXT NOT NULL DEFAULT '',
  archived INTEGER NOT NULL DEFAULT 0,
  active_job_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  citations_json TEXT NOT NULL DEFAULT '[]',
  artifacts_json TEXT NOT NULL DEFAULT '[]',
  proposal_ids_json TEXT NOT NULL DEFAULT '[]',
  model_option_id TEXT,
  status TEXT NOT NULL DEFAULT 'completed',
  finish_reason TEXT,
  token_usage_json TEXT NOT NULL DEFAULT '{}',
  sequence INTEGER NOT NULL DEFAULT 0,
  client_request_id TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_aliases (
  id TEXT PRIMARY KEY,
  alias TEXT NOT NULL,
  alias_key TEXT NOT NULL UNIQUE,
  canonical_name TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  file_path TEXT NOT NULL,
  inspection_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  proposal_ids_json TEXT NOT NULL DEFAULT '[]',
  format TEXT NOT NULL,
  language TEXT NOT NULL,
  template_id TEXT,
  template_mode TEXT NOT NULL DEFAULT 'strict',
  title TEXT NOT NULL,
  outline_json TEXT NOT NULL,
  status TEXT NOT NULL,
  file_path TEXT,
  preview_json TEXT,
  source_thread_id TEXT,
  source_message_ids_json TEXT NOT NULL DEFAULT '[]',
  context_snapshot_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def _model_name_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def _rebuild_model_registry(connection: sqlite3.Connection) -> None:
    profiles = [dict(row) for row in connection.execute("SELECT * FROM model_profiles").fetchall()]
    candidates: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for profile in profiles:
        models = list(dict.fromkeys([profile.get("text_model") or "", *json.loads(profile.get("available_models_json") or "[]")]))
        for model in (item.strip() for item in models if item and item.strip()):
            candidates.setdefault(_model_name_key(model), []).append((profile, model))
    old_to_new: dict[str, str] = {}
    now = utc_now()
    for normalized, entries in candidates.items():
        entries.sort(key=lambda item: (
            int(item[0].get("last_test_ok") or 0), item[0].get("last_tested_at") or "", item[0].get("updated_at") or ""
        ), reverse=True)
        winner, model = entries[0]
        option_id = uuid.uuid5(uuid.NAMESPACE_URL, f"3gpp-model:{normalized}").hex
        current = connection.execute("SELECT profile_id,model_id,revision FROM model_options WHERE id=?", (option_id,)).fetchone()
        revision = int(current["revision"]) if current else 1
        if current and (current["profile_id"] != winner["id"] or current["model_id"] != model):
            revision += 1
        connection.execute(
            """INSERT INTO model_options(id,normalized_name,display_name,profile_id,model_id,revision,updated_at)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(normalized_name) DO UPDATE SET display_name=excluded.display_name,
               profile_id=excluded.profile_id,model_id=excluded.model_id,revision=excluded.revision,updated_at=excluded.updated_at""",
            (option_id, normalized, model, winner["id"], model, revision, now),
        )
        for profile, old_model in entries:
            old_to_new[f"{profile['id']}:{old_model}"] = option_id
    valid_names = set(candidates)
    if valid_names:
        placeholders = ",".join("?" for _ in valid_names)
        connection.execute(f"DELETE FROM model_options WHERE normalized_name NOT IN ({placeholders})", tuple(valid_names))
    else:
        connection.execute("DELETE FROM model_options")
    for old_id, new_id in old_to_new.items():
        connection.execute("UPDATE chat_threads SET default_model_option_id=? WHERE default_model_option_id=?", (new_id, old_id))
        connection.execute("UPDATE chat_messages SET model_option_id=? WHERE model_option_id=?", (new_id, old_id))
        rows = connection.execute("SELECT * FROM analysis_summaries WHERE model_option_id=?", (old_id,)).fetchall()
        for row in rows:
            connection.execute(
                """INSERT OR IGNORE INTO analysis_summaries(proposal_id,model_option_id,source_sha256,prompt_version,summary_json,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                (row["proposal_id"], new_id, row["source_sha256"], row["prompt_version"], row["summary_json"], row["updated_at"]),
            )
        connection.execute("DELETE FROM analysis_summaries WHERE model_option_id=?", (old_id,))


class Database:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.database_path
        self._write_lock = threading.RLock()
        self._pool: queue.LifoQueue[sqlite3.Connection] = queue.LifoQueue(maxsize=6)
        self._pool_created = 0
        self._pool_guard = threading.Lock()
        self.initialize()

    def _new_connection(self) -> sqlite3.Connection:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=30000")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                return connection
            except sqlite3.OperationalError as exc:
                last_error = exc
                if not any(code in str(exc).casefold() for code in ("unable to open", "busy", "locked")):
                    raise
                time.sleep((0.1, 0.3, 0.9)[attempt])
        raise last_error or sqlite3.OperationalError("database unavailable")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            try:
                connection = self._pool.get_nowait()
            except queue.Empty:
                with self._pool_guard:
                    if self._pool_created < 6:
                        connection = self._new_connection()
                        self._pool_created += 1
                if connection is None:
                    connection = self._pool.get(timeout=30)
            yield connection
        except sqlite3.OperationalError as exc:
            if connection is not None and "unable to open" in str(exc).casefold():
                try: connection.close()
                except Exception: pass
                with self._pool_guard: self._pool_created = max(0, self._pool_created - 1)
                connection = None
            raise
        finally:
            if connection is not None:
                self._pool.put(connection)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            report_columns = {row[1] for row in connection.execute("PRAGMA table_info(reports)").fetchall()}
            if "template_mode" not in report_columns:
                connection.execute("ALTER TABLE reports ADD COLUMN template_mode TEXT NOT NULL DEFAULT 'strict'")
            if "proposal_ids_json" not in report_columns:
                connection.execute("ALTER TABLE reports ADD COLUMN proposal_ids_json TEXT NOT NULL DEFAULT '[]'")
            for name, definition in (
                ("source_thread_id", "TEXT"),
                ("source_message_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("context_snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
            ):
                if name not in report_columns:
                    connection.execute(f"ALTER TABLE reports ADD COLUMN {name} {definition}")
            analysis_columns = {row[1] for row in connection.execute("PRAGMA table_info(analyses)").fetchall()}
            if "vision_complete" not in analysis_columns:
                connection.execute("ALTER TABLE analyses ADD COLUMN vision_complete INTEGER NOT NULL DEFAULT 0")
                connection.execute("UPDATE analyses SET vision_complete=1 WHERE visual_count=0")
            if "visual_coverage_json" not in analysis_columns:
                connection.execute("ALTER TABLE analyses ADD COLUMN visual_coverage_json TEXT NOT NULL DEFAULT '{}'")
            proposal_columns = {row[1] for row in connection.execute("PRAGMA table_info(proposals)").fetchall()}
            if "primary_source" not in proposal_columns:
                connection.execute("ALTER TABLE proposals ADD COLUMN primary_source TEXT NOT NULL DEFAULT 'Unknown'")
            if "preparation_state" not in proposal_columns:
                connection.execute("ALTER TABLE proposals ADD COLUMN preparation_state TEXT NOT NULL DEFAULT 'idle'")
            if "preparation_error" not in proposal_columns:
                connection.execute("ALTER TABLE proposals ADD COLUMN preparation_error TEXT")
            if "canonical_source" not in proposal_columns:
                connection.execute("ALTER TABLE proposals ADD COLUMN canonical_source TEXT NOT NULL DEFAULT 'Unknown'")
            for name, definition in (
                ("file_url", "TEXT NOT NULL DEFAULT ''"),
                ("file_name", "TEXT NOT NULL DEFAULT ''"),
                ("file_kind", "TEXT NOT NULL DEFAULT 'zip'"),
                ("file_size", "INTEGER"),
                ("file_modified", "TEXT"),
                ("metadata_source", "TEXT NOT NULL DEFAULT 'index'"),
                ("analyzable", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in proposal_columns:
                    connection.execute(f"ALTER TABLE proposals ADD COLUMN {name} {definition}")
            connection.execute("UPDATE proposals SET file_url=zip_url WHERE file_url='' OR file_url IS NULL")
            connection.execute("UPDATE proposals SET file_name=tdoc||'.zip' WHERE file_name='' OR file_name IS NULL")
            preparation_columns = {row[1] for row in connection.execute("PRAGMA table_info(preparations)").fetchall()}
            for name, definition in (
                ("visual_evidence_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("resolved_visual_count", "INTEGER NOT NULL DEFAULT 0"),
                ("unresolved_visual_count", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in preparation_columns:
                    connection.execute(f"ALTER TABLE preparations ADD COLUMN {name} {definition}")
            message_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_messages)").fetchall()}
            if "artifacts_json" not in message_columns:
                connection.execute("ALTER TABLE chat_messages ADD COLUMN artifacts_json TEXT NOT NULL DEFAULT '[]'")
            for name, definition in (
                ("proposal_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("model_option_id", "TEXT"),
                ("status", "TEXT NOT NULL DEFAULT 'completed'"),
                ("finish_reason", "TEXT"),
                ("token_usage_json", "TEXT NOT NULL DEFAULT '{}'")
            ):
                if name not in message_columns:
                    connection.execute(f"ALTER TABLE chat_messages ADD COLUMN {name} {definition}")
            if "sequence" not in message_columns:
                connection.execute("ALTER TABLE chat_messages ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0")
            needs_sequence_rebuild = connection.execute(
                """SELECT 1 FROM chat_messages GROUP BY thread_id HAVING MIN(sequence)<=0 OR COUNT(*)<>COUNT(DISTINCT sequence) LIMIT 1"""
            ).fetchone()
            if needs_sequence_rebuild:
                threads = connection.execute("SELECT DISTINCT thread_id FROM chat_messages").fetchall()
                for thread in threads:
                    rows = connection.execute("SELECT rowid FROM chat_messages WHERE thread_id=? ORDER BY created_at,rowid", (thread[0],)).fetchall()
                    for sequence, row in enumerate(rows, start=1):
                        connection.execute("UPDATE chat_messages SET sequence=? WHERE rowid=?", (sequence, row[0]))
            if "client_request_id" not in message_columns:
                connection.execute("ALTER TABLE chat_messages ADD COLUMN client_request_id TEXT")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_message_sequence ON chat_messages(thread_id,sequence)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_message_request_role ON chat_messages(thread_id,client_request_id,role) WHERE client_request_id IS NOT NULL")
            thread_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_threads)").fetchall()}
            for name, definition in (
                ("default_model_option_id", "TEXT"),
                ("summary", "TEXT NOT NULL DEFAULT ''"),
                ("archived", "INTEGER NOT NULL DEFAULT 0"),
                ("active_job_id", "TEXT")
            ):
                if name not in thread_columns:
                    connection.execute(f"ALTER TABLE chat_threads ADD COLUMN {name} {definition}")
            profile_columns = {row[1] for row in connection.execute("PRAGMA table_info(model_profiles)").fetchall()}
            for name, definition in (
                ("available_models_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("deployment", "TEXT NOT NULL DEFAULT 'external'"),
                ("max_output_tokens", "INTEGER NOT NULL DEFAULT 8192"),
                ("context_window", "INTEGER NOT NULL DEFAULT 65536"),
                ("max_concurrency", "INTEGER NOT NULL DEFAULT 4"),
                ("request_timeout_seconds", "INTEGER NOT NULL DEFAULT 180"),
                ("enabled", "INTEGER NOT NULL DEFAULT 1"),
                ("last_test_ok", "INTEGER NOT NULL DEFAULT 0"),
                ("last_tested_at", "TEXT"),
            ):
                if name not in profile_columns:
                    connection.execute(f"ALTER TABLE model_profiles ADD COLUMN {name} {definition}")
            job_columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
            if "partial_result_json" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN partial_result_json TEXT")
            if "thread_id" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN thread_id TEXT")
            for name, definition in (("client_request_id", "TEXT"), ("heartbeat_at", "TEXT"), ("stage", "TEXT NOT NULL DEFAULT 'queued'"), ("deadline_at", "TEXT"), ("attempt", "INTEGER NOT NULL DEFAULT 0")):
                if name not in job_columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_thread ON jobs(thread_id,created_at)")
            user_aliases = {
                row["alias_key"]: row["canonical_name"]
                for row in connection.execute("SELECT alias_key,canonical_name FROM source_aliases").fetchall()
            }
            source_rows = [
                {"id": row[0], "meeting_id": row[1], "source": row[2]}
                for row in connection.execute("SELECT id,meeting_id,source FROM proposals").fetchall()
            ]
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in source_rows:
                row["primary_source"] = extract_primary_source(row["source"])
                row["canonical_source"] = canonical_source_name(row["primary_source"], user_aliases)
                grouped.setdefault(row["meeting_id"], []).append(row)
            for meeting_rows in grouped.values():
                apply_meeting_parent_names(meeting_rows, user_aliases)
            for row in source_rows:
                connection.execute(
                    "UPDATE proposals SET primary_source=?,canonical_source=? WHERE id=?",
                    (row["primary_source"], row["canonical_source"], row["id"]),
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_proposals_primary_source ON proposals(meeting_id, primary_source COLLATE NOCASE)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_proposals_canonical_source ON proposals(meeting_id, canonical_source COLLATE NOCASE)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO app_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            _rebuild_model_registry(connection)
            connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            with self.connect() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    yield connection
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with self.connect() as connection:
                    return [dict(row) for row in connection.execute(sql, params).fetchall()]
            except sqlite3.OperationalError as exc:
                last_error = exc
                if not any(code in str(exc).casefold() for code in ("unable to open", "busy", "locked")):
                    raise
                time.sleep((0.1, 0.3, 0.9)[attempt])
        raise last_error or sqlite3.OperationalError("database unavailable")

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def health(self) -> dict[str, Any]:
        try:
            with self.connect() as connection:
                integrity = connection.execute("PRAGMA quick_check(1)").fetchone()[0]
            writable = os.access(self.path.parent, os.W_OK) and (not self.path.exists() or os.access(self.path, os.W_OK))
            ok = integrity == "ok" and writable
            return {"ok": ok, "writable": writable, "path": str(self.path), "pool_size": self._pool_created, "pool_limit": 6}
        except Exception:
            return {"ok": False, "writable": False, "path": str(self.path), "pool_size": self._pool_created, "pool_limit": 6}

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.transaction() as connection:
            connection.execute(sql, params)

    def create_job(self, kind: str, payload: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        now = utc_now()
        self.execute(
            """INSERT INTO jobs(id,kind,status,payload_json,thread_id,client_request_id,heartbeat_at,stage,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (job_id, kind, "queued", json.dumps(payload, ensure_ascii=False), payload.get("thread_id"),
             payload.get("client_request_id"), now, "queued", now, now),
        )
        self.add_job_event(job_id, "progress", {"id": job_id, "status": "queued", "progress": 0, "message": "任务已排队"})
        return job_id

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        message: str | None = None,
        result: dict[str, Any] | None = None,
        partial_result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        current = self.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not current:
            return
        stage_value = current.get("stage", "processing")
        stage_message = message or ""
        for marker, stage_name in (("下载", "download"), ("提取", "extract"), ("图表", "vision"),
                                   ("分析", "analysis"), ("回答", "answer"), ("报告", "report"),
                                   ("终止", "cancelled"), ("失败", "failed"), ("完成", "completed")):
            if marker in stage_message:
                stage_value = stage_name
                break
        self.execute(
            """UPDATE jobs SET status=?, progress=?, message=?, result_json=?,partial_result_json=?, error=?,
               heartbeat_at=?,stage=?,updated_at=? WHERE id=?""",
            (
                status if status is not None else current["status"],
                progress if progress is not None else current["progress"],
                message if message is not None else current["message"],
                json.dumps(result, ensure_ascii=False) if result is not None else current["result_json"],
                json.dumps(partial_result, ensure_ascii=False) if partial_result is not None else current.get("partial_result_json"),
                error if error is not None else current["error"],
                utc_now(),
                stage_value,
                utc_now(),
                job_id,
            ),
        )
        event_status = status if status is not None else current["status"]
        event_time = utc_now()
        payload = json.loads(current.get("payload_json") or "{}")
        self.add_job_event(job_id, event_status if event_status in {"completed", "failed", "cancelled"} else "progress", {
            "id": job_id, "job_id": job_id, "thread_id": current.get("thread_id"),
            "message_id": payload.get("assistant_message_id"), "status": event_status,
            "progress": progress if progress is not None else current["progress"],
            "message": message if message is not None else current["message"],
            "error": error if error is not None else current["error"],
            "partial_result": partial_result,
            "result": result,
            "updated_at": event_time,
        })

    def add_job_event(self, job_id: str, event_type: str, data: dict[str, Any]) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO job_events(job_id,event_type,data_json,created_at) VALUES(?,?,?,?)",
                (job_id, event_type, json.dumps(data, ensure_ascii=False), utc_now()),
            )
            return int(cursor.lastrowid)

db = Database()
