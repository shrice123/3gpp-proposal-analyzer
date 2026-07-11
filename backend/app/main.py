from __future__ import annotations

import asyncio
import json
import shutil
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import APP_VERSION, settings
from .database import db, utc_now
from .jobs import runner
from .models import discover_models, model_options, public_profile, resolve_model_option, save_profile, test_profile
from .reports import create_outline, inspect_template, render_report, save_template
from .schemas import (
    ChatBatchDelete,
    ChatJobRequest,
    ChatThreadCreate,
    ChatThreadUpdate,
    ModelProfileRequest,
    ProposalSelection,
    ReportOutlineRequest,
    ReportJobRequest,
    ReportRenderRequest,
    SourceAliasRequest,
    SourceImportRequest,
)
from .source_normalization import BUILTIN_ALIASES, alias_key, rebuild_canonical_sources, save_source_alias
from .threegpp import import_meeting, list_proposals


@asynccontextmanager
async def lifespan(_: FastAPI):
    runner.start()
    yield
    runner.stop()


app = FastAPI(
    title="3GPP Proposal Analyzer",
    version=APP_VERSION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:5173", "http://127.0.0.1:5173", "null",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    database_status = db.health()
    runner_status = runner.status()
    ok = bool(database_status["ok"] and runner_status["ok"])
    return {"ok": ok, "status": "ok" if ok else "degraded", "version": APP_VERSION,
            "data_dir": str(settings.data_dir), "database": database_status, "runner": runner_status}


@app.get("/api/runtime/status")
def runtime_status() -> dict[str, Any]:
    usage = shutil.disk_usage(settings.data_dir)
    return {
        "version": APP_VERSION,
        "data_dir": str(settings.data_dir),
        "disk_free": usage.free,
        "components": [
            {"name": "Document converter", "installed": bool(shutil.which("soffice") or shutil.which("libreoffice"))},
            {"name": "OCR engine", "installed": bool(shutil.which("tesseract"))},
            {"name": "Local database", "installed": settings.database_path.exists()},
        ],
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    rows = db.query("SELECT key,value_json FROM settings")
    return {row["key"]: json.loads(row["value_json"]) for row in rows}


@app.put("/api/settings/{key}")
def put_setting(key: str, value: dict[str, Any]) -> dict[str, Any]:
    db.execute(
        """INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?)
           ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
        (key, json.dumps(value, ensure_ascii=False), utc_now()),
    )
    return {"ok": True}


@app.get("/api/meetings")
def meetings() -> list[dict[str, Any]]:
    return db.query("SELECT * FROM meetings ORDER BY updated_at DESC")


@app.post("/api/sources/import")
def import_source(payload: SourceImportRequest) -> dict[str, Any]:
    try:
        return import_meeting(str(payload.url))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/sources/import-file")
async def import_source_file(source_url: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 XLSX 索引表")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
        handle.write(await file.read())
        temp_path = Path(handle.name)
    try:
        return import_meeting(source_url, index_path=temp_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)


@app.get("/api/meetings/{meeting_id}/proposals")
def proposals(
    meeting_id: str,
    agenda: list[str] | None = Query(default=None),
    source: list[str] | None = Query(default=None),
    search: str = "",
    limit: int = Query(default=500, ge=1, le=2500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return list_proposals(meeting_id, agenda=agenda, source=source, search=search, limit=limit, offset=offset)


@app.get("/api/source-aliases")
def source_aliases() -> list[dict[str, Any]]:
    user_rows = db.query("SELECT id,alias,canonical_name,updated_at FROM source_aliases ORDER BY alias COLLATE NOCASE")
    user_keys = {alias_key(row["alias"]) for row in user_rows}
    builtins = [
        {"id": f"builtin:{key}", "alias": key, "canonical_name": canonical, "built_in": True}
        for key, canonical in sorted(BUILTIN_ALIASES.items()) if key not in user_keys
    ]
    return [{**row, "built_in": False} for row in user_rows] + builtins


@app.put("/api/source-aliases")
def put_source_alias(payload: SourceAliasRequest) -> dict[str, Any]:
    return save_source_alias(db, payload.alias, payload.canonical_name, utc_now())


@app.delete("/api/source-aliases/{alias_id}")
def delete_source_alias(alias_id: str) -> dict[str, Any]:
    if alias_id.startswith("builtin:"):
        raise HTTPException(status_code=400, detail="内置别名不能删除，可以添加用户规则覆盖")
    db.execute("DELETE FROM source_aliases WHERE id=?", (alias_id,))
    rebuild_canonical_sources(db)
    return {"ok": True}


@app.post("/api/download-jobs")
def create_download_job(payload: ProposalSelection) -> dict[str, Any]:
    return {"id": runner.submit("download", payload.model_dump())}


@app.post("/api/analysis-jobs")
def create_analysis_job(payload: ProposalSelection) -> dict[str, Any]:
    return {"id": runner.submit("analysis", payload.model_dump())}


@app.post("/api/preparation-jobs")
def create_preparation_job(payload: ProposalSelection) -> dict[str, Any]:
    return {"id": runner.submit("prepare", payload.model_dump())}


@app.post("/api/chat-jobs")
def create_chat_job(payload: ChatJobRequest) -> dict[str, Any]:
    values = payload.model_dump()
    values["client_request_id"] = payload.client_request_id or uuid.uuid4().hex
    thread_id = payload.thread_id
    if thread_id:
        thread = db.one("SELECT * FROM chat_threads WHERE id=?", (thread_id,))
        if not thread:
            raise HTTPException(status_code=404, detail="会话不存在")
        values["model_option_id"] = values.get("model_option_id") or thread.get("default_model_option_id")
    else:
        created = create_chat(ChatThreadCreate(
            meeting_id=payload.meeting_id, proposal_ids=payload.proposal_ids,
            title=payload.question[:80], default_model_option_id=payload.model_option_id,
        ))
        thread_id = created["id"]
    values["thread_id"] = thread_id
    if values.get("model_option_id") and not resolve_model_option(values["model_option_id"])[0]:
        raise HTTPException(status_code=400, detail="所选模型不可用，请重新配置")
    now = utc_now()
    job_id = uuid.uuid4().hex
    user_message_id = uuid.uuid4().hex
    assistant_message_id = uuid.uuid4().hex
    values.update({"user_message_id": user_message_id, "assistant_message_id": assistant_message_id})
    with db.transaction() as connection:
        duplicate = connection.execute(
            "SELECT id,status,payload_json FROM jobs WHERE thread_id=? AND client_request_id=?",
            (thread_id, values["client_request_id"]),
        ).fetchone()
        if duplicate:
            old_payload = json.loads(duplicate["payload_json"])
            return {"id": duplicate["id"], "thread_id": thread_id,
                    "user_message_id": old_payload.get("user_message_id"),
                    "assistant_message_id": old_payload.get("assistant_message_id"),
                    "resolved_mode": old_payload.get("resolved_mode")}
        active_id = connection.execute("SELECT active_job_id FROM chat_threads WHERE id=?", (thread_id,)).fetchone()[0]
        if active_id:
            active = connection.execute("SELECT id,status,stage,progress,message,updated_at FROM jobs WHERE id=?", (active_id,)).fetchone()
            if not active or active["status"] in {"completed", "failed", "cancelled"}:
                connection.execute("UPDATE chat_threads SET active_job_id=NULL WHERE id=?", (thread_id,))
            else:
                try:
                    age = (datetime.now(timezone.utc) - datetime.fromisoformat(active["updated_at"])).total_seconds()
                except Exception:
                    age = 0
                stale = (active["status"] == "queued" and age > 120) or (active["status"] == "running" and age > 660)
                if stale:
                    connection.execute("UPDATE jobs SET status='failed',message='任务心跳超时',error='stale_job',updated_at=? WHERE id=?", (now, active_id))
                    connection.execute("UPDATE chat_threads SET active_job_id=NULL WHERE id=?", (thread_id,))
                else:
                    raise HTTPException(status_code=409, detail={"message": "当前会话已有任务正在运行", "active_job": dict(active)})
        local_mode = runner._local_chat_mode(payload.question)
        resolved_mode = payload.chat_mode if payload.chat_mode != "auto" else (local_mode or ("proposal" if payload.proposal_ids else "general"))
        values["resolved_mode"] = resolved_mode
        sequence = connection.execute("SELECT COALESCE(MAX(sequence),0) FROM chat_messages WHERE thread_id=?", (thread_id,)).fetchone()[0]
        connection.execute(
            """INSERT INTO chat_messages(id,thread_id,role,content,citations_json,artifacts_json,proposal_ids_json,
               model_option_id,status,token_usage_json,sequence,client_request_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,'{}',?,?,?)""",
            (user_message_id, thread_id, "user", payload.question, "[]", "[]", json.dumps(payload.proposal_ids),
             values.get("model_option_id"), "completed", sequence + 1, values["client_request_id"], now),
        )
        connection.execute(
            """INSERT INTO chat_messages(id,thread_id,role,content,citations_json,artifacts_json,proposal_ids_json,
               model_option_id,status,token_usage_json,sequence,client_request_id,created_at)
               VALUES(?,?,?,'','[]','[]',?,?,?,'{}',?,?,?)""",
            (assistant_message_id, thread_id, "assistant", json.dumps(payload.proposal_ids), values.get("model_option_id"),
             "streaming", sequence + 2, values["client_request_id"], now),
        )
        connection.execute(
            """INSERT INTO jobs(id,kind,status,progress,message,payload_json,thread_id,client_request_id,
               heartbeat_at,stage,attempt,created_at,updated_at) VALUES(?,'chat','queued',0,'任务已排队',?,?,?,?,'queued',0,?,?)""",
            (job_id, json.dumps(values, ensure_ascii=False), thread_id, values["client_request_id"], now, now, now),
        )
        if payload.proposal_ids:
            connection.execute(
                "UPDATE chat_threads SET active_job_id=?,default_model_option_id=COALESCE(?,default_model_option_id),proposal_ids_json=?,updated_at=? WHERE id=?",
                (job_id, values.get("model_option_id"), json.dumps(payload.proposal_ids), now, thread_id),
            )
        else:
            connection.execute(
                "UPDATE chat_threads SET active_job_id=?,default_model_option_id=COALESCE(?,default_model_option_id),updated_at=? WHERE id=?",
                (job_id, values.get("model_option_id"), now, thread_id),
            )
    db.add_job_event(job_id, "progress", {"id": job_id, "thread_id": thread_id, "message_id": assistant_message_id,
                                                   "status": "queued", "progress": 0, "message": "任务已排队"})
    runner.enqueue_existing(job_id, "chat")
    return {"id": job_id, "thread_id": thread_id, "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id, "resolved_mode": values["resolved_mode"]}


@app.post("/api/report-jobs")
def create_report_job(payload: ReportJobRequest) -> dict[str, Any]:
    job_id = runner.submit("report", payload.model_dump())
    if payload.thread_id:
        db.execute("UPDATE chat_threads SET active_job_id=?,updated_at=? WHERE id=?", (job_id, utc_now(), payload.thread_id))
    return {"id": job_id}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    runner.cancel(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("payload_json"):
        job["payload"] = json.loads(job.pop("payload_json"))
    if job.get("result_json"):
        job["result"] = json.loads(job.pop("result_json"))
    if job.get("partial_result_json"):
        job["partial_result"] = json.loads(job.pop("partial_result_json"))
    return job


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, last_event_id: int | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
    async def stream():
        cursor = int(last_event_id or 0)
        while True:
            try:
                rows = db.query("SELECT id,event_type,data_json FROM job_events WHERE job_id=? AND id>? ORDER BY id LIMIT 200", (job_id, cursor))
            except Exception:
                yield f"event: recoverable_error\ndata: {json.dumps({'job_id': job_id, 'retryable': True, 'message': '本地数据服务暂时繁忙，正在重新连接'}, ensure_ascii=False)}\n\n"
                break
            for row in rows:
                cursor = row["id"]
                yield f"id: {cursor}\nevent: {row['event_type']}\ndata: {row['data_json']}\n\n"
            try:
                job = get_job(job_id)
            except Exception:
                yield f"event: recoverable_error\ndata: {json.dumps({'job_id': job_id, 'retryable': True, 'message': '本地数据服务暂时繁忙，正在重新连接'}, ensure_ascii=False)}\n\n"
                break
            if job["status"] in {"completed", "failed", "cancelled"}:
                if not rows:
                    yield f"event: {job['status']}\ndata: {json.dumps(job, ensure_ascii=False)}\n\n"
                break
            if not rows:
                yield f"event: heartbeat\ndata: {json.dumps({'job_id': job_id, 'updated_at': job['updated_at']})}\n\n"
            await asyncio.sleep(0.75)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _job_file(job_id: str, expected_kind: str) -> Path:
    job = get_job(job_id)
    if job["kind"] != expected_kind or job["status"] != "completed" or not job.get("result"):
        raise HTTPException(status_code=409, detail="文件尚未生成")
    path = Path(job["result"]["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件已被删除")
    return path


@app.get("/api/download-jobs/{job_id}/file")
def download_package(job_id: str) -> FileResponse:
    path = _job_file(job_id, "download")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@app.delete("/api/download-jobs/{job_id}/file")
def delete_package(job_id: str) -> dict[str, Any]:
    path = _job_file(job_id, "download")
    path.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/download-jobs")
def download_history() -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM jobs WHERE kind='download' ORDER BY created_at DESC LIMIT 100")
    for row in rows:
        row["result"] = json.loads(row["result_json"]) if row.get("result_json") else None
        row.pop("result_json", None)
        row.pop("payload_json", None)
    return rows


@app.get("/api/model-profiles")
def model_profiles() -> list[dict[str, Any]]:
    return [public_profile(row) for row in db.query("SELECT * FROM model_profiles ORDER BY is_default DESC,updated_at DESC")]


@app.post("/api/model-profiles")
def create_model_profile(payload: ModelProfileRequest) -> dict[str, Any]:
    return save_profile(payload.model_dump())


@app.get("/api/model-options")
def available_model_options() -> list[dict[str, Any]]:
    return model_options()


@app.post("/api/model-profiles/{profile_id}/discover")
def discover_profile_models(profile_id: str) -> dict[str, Any]:
    try:
        return {"models": discover_models(profile_id)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/model-profiles/test")
def test_model_profile(payload: ModelProfileRequest) -> dict[str, Any]:
    return test_profile(payload.model_dump())


@app.get("/api/chats")
def chats(
    meeting_id: str | None = None,
    state: str = Query(default="active", pattern="^(active|archived|all)$"),
    include_archived: bool | None = None,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if meeting_id:
        clauses.append("t.meeting_id=?")
        params.append(meeting_id)
    if include_archived is True:
        state = "all"
    if state == "active":
        clauses.append("t.archived=0")
    elif state == "archived":
        clauses.append("t.archived=1")
    return db.query(
        f"""SELECT t.*,COUNT(DISTINCT m.id) AS message_count,COUNT(DISTINCT r.id) AS report_count,
            COALESCE(mt.name,'') AS meeting_name FROM chat_threads t
            LEFT JOIN chat_messages m ON m.thread_id=t.id
            LEFT JOIN reports r ON r.source_thread_id=t.id
            LEFT JOIN meetings mt ON mt.id=t.meeting_id
            WHERE {' AND '.join(clauses)}
            GROUP BY t.id ORDER BY t.updated_at DESC""", tuple(params)
    )


@app.get("/api/chats/{thread_id}/active-job")
def active_chat_job(thread_id: str) -> dict[str, Any]:
    thread = db.one("SELECT active_job_id FROM chat_threads WHERE id=?", (thread_id,))
    if not thread:
        raise HTTPException(status_code=404, detail="会话不存在")
    job_id = thread.get("active_job_id")
    if not job_id:
        return {"job": None}
    job = db.one("SELECT id,kind,status,progress,message,updated_at,thread_id FROM jobs WHERE id=?", (job_id,))
    if not job or job["status"] in {"completed", "failed", "cancelled"}:
        db.execute("UPDATE chat_threads SET active_job_id=NULL WHERE id=?", (thread_id,))
        return {"job": None}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(job["updated_at"])).total_seconds()
    except Exception:
        age = 0
    if (job["status"] == "queued" and age > 120) or (job["status"] == "running" and age > 660):
        db.update_job(job_id, status="failed", message="任务心跳超时", error="stale_job")
        db.execute("UPDATE chat_threads SET active_job_id=NULL WHERE id=?", (thread_id,))
        return {"job": None}
    return {"job": job}


@app.post("/api/chats")
def create_chat(payload: ChatThreadCreate) -> dict[str, Any]:
    thread_id = uuid.uuid4().hex
    now = utc_now()
    db.execute(
        """INSERT INTO chat_threads(id,meeting_id,proposal_ids_json,title,default_model_option_id,
           summary,archived,created_at,updated_at) VALUES(?,?,?,?,?,'',0,?,?)""",
        (thread_id, payload.meeting_id, json.dumps(payload.proposal_ids), payload.title,
         payload.default_model_option_id, now, now),
    )
    return db.one("SELECT * FROM chat_threads WHERE id=?", (thread_id,)) or {}


@app.patch("/api/chats/{thread_id}")
def update_chat(thread_id: str, payload: ChatThreadUpdate) -> dict[str, Any]:
    thread = db.one("SELECT * FROM chat_threads WHERE id=?", (thread_id,))
    if not thread:
        raise HTTPException(status_code=404, detail="会话不存在")
    db.execute(
        "UPDATE chat_threads SET title=?,archived=?,default_model_option_id=?,updated_at=? WHERE id=?",
        (payload.title if payload.title is not None else thread["title"],
         int(payload.archived) if payload.archived is not None else thread["archived"],
         payload.default_model_option_id if payload.default_model_option_id is not None else thread.get("default_model_option_id"),
         utc_now(), thread_id),
    )
    return db.one("SELECT * FROM chat_threads WHERE id=?", (thread_id,)) or {}


def _delete_archived_threads(thread_ids: list[str]) -> tuple[int, list[dict[str, Any]]]:
    if not thread_ids:
        return 0, []
    placeholders = ",".join("?" for _ in thread_ids)
    rows = db.query(
        f"SELECT id,archived,active_job_id FROM chat_threads WHERE id IN ({placeholders})", tuple(thread_ids)
    )
    if len(rows) != len(set(thread_ids)):
        raise HTTPException(status_code=404, detail="部分会话不存在")
    if any(not row["archived"] for row in rows):
        raise HTTPException(status_code=409, detail="只能永久删除已归档会话")
    for row in rows:
        if row.get("active_job_id"):
            runner.cancel(row["active_job_id"])
    reports = db.query(
        f"SELECT file_path,preview_json FROM reports WHERE source_thread_id IN ({placeholders})", tuple(thread_ids)
    )
    with db.transaction() as connection:
        connection.execute(f"DELETE FROM reports WHERE source_thread_id IN ({placeholders})", tuple(thread_ids))
        connection.execute(f"DELETE FROM jobs WHERE thread_id IN ({placeholders})", tuple(thread_ids))
        connection.execute(f"DELETE FROM chat_threads WHERE id IN ({placeholders})", tuple(thread_ids))
    return len(rows), reports


def _remove_report_files(reports: list[dict[str, Any]]) -> None:
    for report in reports:
        preview = json.loads(report.get("preview_json") or "{}")
        for candidate in (report.get("file_path"), preview.get("preview_file")):
            if candidate:
                Path(candidate).unlink(missing_ok=True)


@app.post("/api/chats/batch-delete")
def batch_delete_chats(payload: ChatBatchDelete) -> dict[str, Any]:
    deleted, reports = _delete_archived_threads(payload.thread_ids)
    _remove_report_files(reports)
    return {"ok": True, "deleted": deleted, "report_count": len(reports)}


@app.delete("/api/chats/{thread_id}")
def delete_chat(thread_id: str) -> dict[str, Any]:
    thread = db.one("SELECT archived,active_job_id FROM chat_threads WHERE id=?", (thread_id,))
    if not thread:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not thread["archived"]:
        raise HTTPException(status_code=409, detail="请先归档会话，再永久删除")
    _, reports = _delete_archived_threads([thread_id])
    _remove_report_files(reports)
    return {"ok": True}


@app.get("/api/chats/{thread_id}/messages")
def chat_messages(thread_id: str) -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM chat_messages WHERE thread_id=? ORDER BY sequence,created_at,rowid", (thread_id,))
    for row in rows:
        row["citations"] = json.loads(row.pop("citations_json") or "[]")
        row["artifacts"] = json.loads(row.pop("artifacts_json") or "[]")
        row["proposal_ids"] = json.loads(row.pop("proposal_ids_json") or "[]")
        row["token_usage"] = json.loads(row.pop("token_usage_json") or "{}")
    return rows


@app.post("/api/templates/inspect")
async def upload_template(file: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".docx", ".pptx"}:
        raise HTTPException(status_code=400, detail="仅支持 DOCX 或 PPTX")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(await file.read())
        path = Path(handle.name)
    try:
        return save_template(path, suffix[1:], file.filename or f"template{suffix}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)


@app.get("/api/templates")
def templates() -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM templates ORDER BY created_at DESC")
    for row in rows:
        row["inspection"] = json.loads(row.pop("inspection_json"))
        row.pop("file_path", None)
    return rows


@app.post("/api/reports/outlines")
def new_report(payload: ReportOutlineRequest) -> dict[str, Any]:
    try:
        return create_outline(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/reports/{report_id}/render")
def generate_report(report_id: str, payload: ReportRenderRequest) -> dict[str, Any]:
    try:
        return render_report(report_id, payload.outline)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/reports/{report_id}/file")
def report_file(report_id: str) -> FileResponse:
    report = db.one("SELECT * FROM reports WHERE id=?", (report_id,))
    if not report or not report.get("file_path"):
        raise HTTPException(status_code=404, detail="报告不存在")
    path = Path(report["file_path"])
    return FileResponse(path, filename=path.name)


@app.get("/api/reports/{report_id}/preview")
def report_preview(report_id: str) -> FileResponse:
    report = db.one("SELECT preview_json FROM reports WHERE id=?", (report_id,))
    preview = json.loads(report["preview_json"]) if report and report.get("preview_json") else {}
    path = Path(preview.get("preview_file") or "")
    if not path.exists() or path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="当前环境未生成 PDF 预览")
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@app.get("/api/diagnostics/export")
def diagnostics() -> FileResponse:
    path = settings.logs_dir / f"diagnostics-{int(time.time())}.json"
    status = runtime_status()
    status["meetings"] = len(db.query("SELECT id FROM meetings"))
    status["jobs"] = db.query("SELECT kind,status,COUNT(*) AS count FROM jobs GROUP BY kind,status")
    status["api_keys_included"] = False
    path.write_text(json.dumps(status, ensure_ascii=False, indent=2), "utf-8")
    return FileResponse(path, filename=path.name, media_type="application/json")


def _web_dist_path() -> Path | None:
    candidates = [
        Path(os.environ["PROPOSAL_WEB_DIST"]) if os.environ.get("PROPOSAL_WEB_DIST") else None,
        Path(getattr(sys, "_MEIPASS", "")) / "desktop-dist" if getattr(sys, "_MEIPASS", None) else None,
        Path(__file__).resolve().parents[2] / "desktop-dist",
    ]
    return next((path for path in candidates if path and (path / "index.html").exists()), None)


web_dist = _web_dist_path()
if web_dist:
    app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")
