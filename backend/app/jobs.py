from __future__ import annotations

import csv
import hashlib
import itertools
import json
import queue
import shutil
import threading
import time
import uuid
import zipfile
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .config import settings
from .database import Database, db, utc_now
from .extractors import extract_document_isolated, safe_extract
from .models import call_vision_model, resolve_model_option, stream_text_model, summarize_with_model
from .reports import create_outline, render_report
from .threegpp import download_file


TERMINAL_STATES = {"completed", "failed", "cancelled"}
JOB_PRIORITIES = {"chat": 0, "report": 1, "analysis": 2, "download": 5, "prepare": 10}


class JobRunner:
    def __init__(self, database: Database = db):
        self.database = database
        self.queue: queue.PriorityQueue[tuple[int, int, str]] = queue.PriorityQueue()
        self._sequence = itertools.count()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._proposal_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._parse_slots = threading.Semaphore(2)
        self._vision_slots = threading.Semaphore(2)
        self.handlers: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
            "download": self._download_job,
            "prepare": self._prepare_job,
            "analysis": self._analysis_job,
            "chat": self._chat_job,
            "report": self._report_job,
        }

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads):
            return
        self._stop.clear()
        self.database.execute(
            "UPDATE jobs SET status='queued', message='应用重启后等待恢复' WHERE status IN ('running','cancelling')"
        )
        self.database.execute(
            """UPDATE chat_threads SET active_job_id=NULL WHERE active_job_id IS NOT NULL AND
               NOT EXISTS(SELECT 1 FROM jobs j WHERE j.id=chat_threads.active_job_id AND j.status IN ('queued','running'))"""
        )
        self.database.execute(
            """UPDATE chat_messages SET status='failed',finish_reason='recovered',
               content=CASE WHEN content='' THEN '上次任务异常中断，可重新发送问题。' ELSE content END
               WHERE status='streaming' AND NOT EXISTS(
                 SELECT 1 FROM jobs j WHERE j.thread_id=chat_messages.thread_id AND j.status IN ('queued','running'))"""
        )
        for item in self.database.query("SELECT id,kind FROM jobs WHERE status='queued' ORDER BY created_at"):
            self._enqueue(item["id"], item["kind"])
        self._threads = [
            threading.Thread(target=self._loop, name=f"proposal-job-runner-{index}", daemon=True)
            for index in range(6)
        ]
        for thread in self._threads:
            thread.start()
        watchdog = threading.Thread(target=self._watchdog, name="proposal-job-watchdog", daemon=True)
        watchdog.start()
        self._threads.append(watchdog)

    def stop(self) -> None:
        self._stop.set()

    def _enqueue(self, job_id: str, kind: str) -> None:
        self.queue.put((JOB_PRIORITIES.get(kind, 20), next(self._sequence), job_id))

    def enqueue_existing(self, job_id: str, kind: str) -> None:
        self._cancel_events[job_id] = threading.Event()
        self._enqueue(job_id, kind)

    def status(self) -> dict[str, Any]:
        workers = [thread for thread in self._threads if "runner" in thread.name]
        return {"ok": bool(workers) and any(thread.is_alive() for thread in workers),
                "workers": sum(thread.is_alive() for thread in workers), "queued": self.queue.qsize()}

    def _watchdog(self) -> None:
        while not self._stop.wait(5):
            try:
                expired = self.database.query(
                    """SELECT DISTINCT j.id FROM jobs j JOIN job_items i ON i.job_id=j.id
                       WHERE j.status='running' AND i.status='running' AND i.deadline_at IS NOT NULL AND i.deadline_at<?""",
                    (utc_now(),),
                )
                for item in expired:
                    self.cancel(item["id"])
            except Exception:
                continue

    def submit(self, kind: str, payload: dict[str, Any]) -> str:
        job_id = self.database.create_job(kind, payload)
        self._cancel_events[job_id] = threading.Event()
        self._enqueue(job_id, kind)
        if kind == "prepare":
            ids = payload.get("proposal_ids", [])
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.database.execute(
                    f"UPDATE proposals SET preparation_state='queued',preparation_error=NULL WHERE id IN ({placeholders}) AND preparation_state NOT IN ('ready','extracting','downloading')",
                    tuple(ids),
                )
        return job_id

    def cancel(self, job_id: str) -> None:
        job = self.database.one("SELECT status,thread_id,payload_json FROM jobs WHERE id=?", (job_id,))
        if job and job["status"] not in TERMINAL_STATES:
            self._cancel_events.setdefault(job_id, threading.Event()).set()
            self.database.update_job(job_id, status="cancelled", message="任务已终止")
            if job.get("thread_id"):
                self.database.execute(
                    "UPDATE chat_threads SET active_job_id=NULL,updated_at=? WHERE id=? AND active_job_id=?",
                    (utc_now(), job["thread_id"], job_id),
                )
                payload = json.loads(job.get("payload_json") or "{}")
                if payload.get("assistant_message_id"):
                    self.database.execute(
                        "UPDATE chat_messages SET status='cancelled',finish_reason='cancelled' WHERE id=? AND status='streaming'",
                        (payload["assistant_message_id"],),
                    )
                else:
                    self.database.execute(
                        "UPDATE chat_messages SET status='cancelled',finish_reason='cancelled' WHERE thread_id=? AND role='assistant' AND status='streaming'",
                        (job["thread_id"],),
                    )

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = None
            try:
                _, _, job_id = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                job = self.database.one("SELECT * FROM jobs WHERE id=?", (job_id,))
                if not job or job["status"] in TERMINAL_STATES:
                    continue
                handler = self.handlers.get(job["kind"])
                if not handler:
                    self.database.update_job(job_id, status="failed", error="未知任务类型", message="任务失败")
                    continue
                self.database.update_job(job_id, status="running", progress=0, message="任务已开始")
                result = handler(job_id, json.loads(job["payload_json"]))
                latest = self.database.one("SELECT status FROM jobs WHERE id=?", (job_id,))
                if latest and latest["status"] not in TERMINAL_STATES:
                    self.database.update_job(job_id, status="completed", progress=1, message="任务已完成", result=result)
            except Exception as exc:
                latest = self.database.one("SELECT status FROM jobs WHERE id=?", (job_id,))
                if not latest or latest["status"] not in TERMINAL_STATES:
                    self.database.update_job(
                        job_id, status="failed", error=str(exc), message="任务失败",
                        result={
                            "thread_id": job.get("thread_id") if job else None,
                            "error_stage": "model" if "模型" in str(exc) else "processing",
                            "error_code": type(exc).__name__, "retryable": True,
                        },
                    )
                    if job and job.get("thread_id"):
                        payload = json.loads(job.get("payload_json") or "{}")
                        if payload.get("assistant_message_id"):
                            self.database.execute(
                                "UPDATE chat_messages SET status='failed',finish_reason='error' WHERE id=? AND status='streaming'",
                                (payload["assistant_message_id"],),
                            )
            finally:
                if job and job.get("kind") in {"chat", "report"}:
                    try:
                        payload = json.loads(job["payload_json"])
                        if payload.get("thread_id"):
                            self.database.execute(
                                "UPDATE chat_threads SET active_job_id=NULL,updated_at=? WHERE id=? AND active_job_id=?",
                                (utc_now(), payload["thread_id"], job_id),
                            )
                    except Exception:
                        pass
                self._cancel_events.pop(job_id, None)
                self.queue.task_done()

    def _selected_proposals(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        ids = payload.get("proposal_ids", [])
        if not ids:
            raise ValueError("未选择提案")
        placeholders = ",".join("?" for _ in ids)
        proposals = self.database.query(
            f"SELECT * FROM proposals WHERE meeting_id=? AND id IN ({placeholders}) ORDER BY agenda_sort,tdoc",
            tuple([payload["meeting_id"], *ids]),
        )
        if not proposals:
            raise ValueError("未找到所选提案")
        return proposals

    def _is_cancelling(self, job_id: str) -> bool:
        if self._cancel_events.get(job_id) and self._cancel_events[job_id].is_set():
            return True
        row = self.database.one("SELECT status FROM jobs WHERE id=?", (job_id,))
        return bool(row and row["status"] in {"cancelling", "cancelled"})

    def _item_status(self, job_id: str, proposal: dict[str, Any], stage: str, status: str, *, attempt: int = 0, error: str | None = None) -> None:
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=600 if stage == "preparation" else 180)).isoformat() if status == "running" else None
        self.database.execute(
            """INSERT INTO job_items(job_id,proposal_id,tdoc,stage,status,attempt,heartbeat_at,deadline_at,error)
               VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,proposal_id,stage) DO UPDATE SET
               status=excluded.status,attempt=excluded.attempt,heartbeat_at=excluded.heartbeat_at,
               deadline_at=excluded.deadline_at,error=excluded.error""",
            (job_id, proposal["id"], proposal["tdoc"], stage, status, attempt, utc_now(), deadline, error),
        )

    @staticmethod
    def _intent(question: str, report: bool = False) -> tuple[str, str]:
        value = question.casefold()
        if report:
            return "report", "正在整理本会话结论并生成报告大纲"
        if any(word in value for word in ("比较", "差异", "对比", "compare", "difference")):
            return "compare", "正在建立所选提案的差异矩阵"
        if any(word in value for word in ("风险", "争议", "依赖", "risk", "conflict", "dependency")):
            return "risk", "正在梳理依赖、争议与潜在风险"
        if any(word in value for word in ("总结", "归纳", "观点", "summary", "summarize")):
            return "summary", "正在归纳各公司的主要技术观点"
        return "answer", "正在定位与问题最相关的提案证据"

    def _adaptive_scope(self, proposals: list[dict[str, Any]], question: str, exhaustive: bool) -> list[dict[str, Any]]:
        if exhaustive or len(proposals) <= 8:
            return proposals
        tokens = [token.casefold() for token in re.findall(r"[A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", question)]
        by_id = {item["id"]: item for item in proposals}
        fts_ranked: list[dict[str, Any]] = []
        if tokens:
            try:
                placeholders = ",".join("?" for _ in proposals)
                query = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:12])
                matches = self.database.query(
                    f"SELECT proposal_id,bm25(analysis_fts) AS score FROM analysis_fts WHERE analysis_fts MATCH ? AND proposal_id IN ({placeholders}) ORDER BY score LIMIT 12",
                    tuple([query, *by_id]),
                )
                fts_ranked = [by_id[item["proposal_id"]] for item in matches if item["proposal_id"] in by_id]
            except Exception:
                fts_ranked = []
        scored = []
        for proposal in proposals:
            haystack = f"{proposal['tdoc']} {proposal['title']} {proposal.get('abstract', '')} {proposal.get('agenda_description', '')}".casefold()
            score = sum(haystack.count(token) for token in tokens)
            scored.append((score, proposal))
        ranked = [item[1] for item in sorted(scored, key=lambda item: (-item[0], item[1]["tdoc"]))]
        combined = list(dict.fromkeys([item["id"] for item in [*fts_ranked, *ranked]]))[:12]
        return [by_id[item_id] for item_id in combined]

    def _proposal_lock(self, proposal_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._proposal_locks.setdefault(proposal_id, threading.Lock())

    def _ensure_zip(self, proposal: dict[str, Any], cancelled: Callable[[], bool] | None = None) -> tuple[Path, dict[str, Any]]:
        cache_path = settings.cache_dir / "sources" / proposal["id"] / (proposal.get("file_name") or f"{proposal['tdoc']}.zip")
        if cache_path.exists() and cache_path.stat().st_size > 0 and zipfile.is_zipfile(cache_path):
            self.database.execute("UPDATE proposals SET download_state='downloaded' WHERE id=?", (proposal["id"],))
            return cache_path, {"bytes": cache_path.stat().st_size, "cached": True}
        cache_path.unlink(missing_ok=True)
        self.database.execute(
            "UPDATE proposals SET download_state='downloading',preparation_state='downloading',preparation_error=NULL WHERE id=?",
            (proposal["id"],),
        )
        meeting = self.database.one("SELECT source_url FROM meetings WHERE id=?", (proposal["meeting_id"],))
        metadata = download_file(proposal.get("file_url") or proposal["zip_url"], cache_path, meeting["source_url"] if meeting else None, cancelled)
        if not zipfile.is_zipfile(cache_path):
            cache_path.unlink(missing_ok=True)
            raise ValueError("下载文件不是有效 ZIP")
        self.database.execute("UPDATE proposals SET download_state='downloaded' WHERE id=?", (proposal["id"],))
        return cache_path, metadata

    def _ensure_source(self, proposal: dict[str, Any], cancelled: Callable[[], bool] | None = None) -> tuple[Path, dict[str, Any]]:
        kind = (proposal.get("file_kind") or "zip").casefold()
        if not proposal.get("analyzable", 1):
            raise ValueError("该文件格式不支持分析")
        if kind == "zip":
            try:
                return self._ensure_zip(proposal, cancelled)
            except TypeError:
                return self._ensure_zip(proposal)
        if kind not in {"docx", "pptx", "pdf", "xlsx"}:
            raise ValueError(f"暂不支持分析 {kind or '未知'} 格式")
        name = proposal.get("file_name") or f"{proposal['tdoc']}.{kind}"
        cache_path = settings.cache_dir / "sources" / proposal["id"] / name
        if cache_path.exists() and cache_path.stat().st_size > 0:
            self.database.execute("UPDATE proposals SET download_state='downloaded' WHERE id=?", (proposal["id"],))
            return cache_path, {"bytes": cache_path.stat().st_size, "cached": True}
        meeting = self.database.one("SELECT source_url FROM meetings WHERE id=?", (proposal["meeting_id"],))
        self.database.execute(
            "UPDATE proposals SET download_state='downloading',preparation_state='downloading',preparation_error=NULL WHERE id=?",
            (proposal["id"],),
        )
        metadata = download_file(
            proposal.get("file_url") or proposal["zip_url"], cache_path,
            meeting["source_url"] if meeting else None, cancelled,
        )
        self.database.execute("UPDATE proposals SET download_state='downloaded' WHERE id=?", (proposal["id"],))
        return cache_path, metadata

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _prepare_proposal(
        self, proposal: dict[str, Any], status: Callable[[str], None] | None = None,
        job_id: str | None = None, attempt: int = 0,
    ) -> dict[str, Any]:
        with self._proposal_lock(proposal["id"]):
            try:
                if job_id:
                    self._item_status(job_id, proposal, "preparation", "running", attempt=attempt)
                if status:
                    status(f"正在检查或下载 {proposal['tdoc']}")
                try:
                    archive, metadata = self._ensure_source(proposal, (lambda: self._is_cancelling(job_id)) if job_id else None)
                except TypeError:
                    # Preserve compatibility with injected/legacy single-argument download adapters.
                    archive, metadata = self._ensure_source(proposal)
                source_sha256 = self._sha256(archive)
                cached = self.database.one("SELECT * FROM preparations WHERE proposal_id=?", (proposal["id"],))
                if cached and cached["source_sha256"] == source_sha256:
                    self.database.execute(
                        "UPDATE proposals SET preparation_state='ready',preparation_error=NULL WHERE id=?",
                        (proposal["id"],),
                    )
                    if job_id:
                        self._item_status(job_id, proposal, "preparation", "completed", attempt=attempt)
                    return {"proposal_id": proposal["id"], "tdoc": proposal["tdoc"], "cached": True}

                self.database.execute(
                    "UPDATE proposals SET preparation_state='extracting',preparation_error=NULL WHERE id=?",
                    (proposal["id"],),
                )
                if status:
                    status(f"正在提取正文、表格和图表素材 · {proposal['tdoc']}")
                with self._parse_slots:
                    extract_dir = settings.cache_dir / "extracted" / proposal["id"]
                    conversion_dir = settings.cache_dir / "converted" / proposal["id"]
                    shutil.rmtree(extract_dir, ignore_errors=True)
                    shutil.rmtree(conversion_dir, ignore_errors=True)
                    files = safe_extract(archive, extract_dir) if (proposal.get("file_kind") or "zip") == "zip" else [archive]
                    documents = [
                        extract_document_isolated(path, conversion_dir, 300, (lambda: self._is_cancelling(job_id)) if job_id else None)
                        for path in files
                    ]
                content = "\n\n".join(
                    f"[{document['file']}]\n{document['text']}" for document in documents if document.get("text")
                )
                if not content and not any(document.get("visual_files") for document in documents):
                    raise ValueError("文稿中没有可提取的正文或图像")
                citations = [
                    {"tdoc": proposal["tdoc"], "artifact_name": document["file"], "page_or_slide": 1}
                    for document in documents if document.get("status") == "ok"
                ]
                visual_files = [
                    path for document in documents for path in document.get("visual_files", [])
                ][:24]
                visual_count = sum(int(document.get("visual_count", 0)) for document in documents)
                visual_evidence = [
                    {**evidence, "tdoc": proposal["tdoc"]}
                    for document in documents for evidence in document.get("visual_evidence", [])
                ]
                if visual_evidence:
                    content += "\n\n[Local visual evidence]\n" + "\n".join(
                        f"- {item['artifact']} / {item.get('part', item.get('kind'))}: {item.get('summary', '')}"
                        for item in visual_evidence
                    )
                resolved_visual_count = sum(1 for item in visual_evidence if item.get("resolved"))
                unresolved_visual_count = sum(1 for item in visual_evidence if not item.get("resolved"))
                document_metadata = [
                    {key: value for key, value in document.items() if key != "text"}
                    for document in documents
                ]
                with self.database.transaction() as connection:
                    connection.execute(
                        """INSERT INTO preparations(proposal_id,content,documents_json,citations_json,
                           visual_files_json,source_sha256,visual_count,visual_evidence_json,
                           resolved_visual_count,unresolved_visual_count,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(proposal_id) DO UPDATE SET content=excluded.content,
                           documents_json=excluded.documents_json,citations_json=excluded.citations_json,
                           visual_files_json=excluded.visual_files_json,source_sha256=excluded.source_sha256,
                           visual_count=excluded.visual_count,visual_evidence_json=excluded.visual_evidence_json,
                           resolved_visual_count=excluded.resolved_visual_count,
                           unresolved_visual_count=excluded.unresolved_visual_count,updated_at=excluded.updated_at""",
                        (
                            proposal["id"], content, json.dumps(document_metadata, ensure_ascii=False),
                            json.dumps(citations, ensure_ascii=False), json.dumps(visual_files, ensure_ascii=False),
                            source_sha256, visual_count, json.dumps(visual_evidence, ensure_ascii=False),
                            resolved_visual_count, unresolved_visual_count, utc_now(),
                        ),
                    )
                    connection.execute(
                        "UPDATE proposals SET preparation_state='ready',preparation_error=NULL WHERE id=?",
                        (proposal["id"],),
                    )
                    connection.execute("DELETE FROM analyses WHERE proposal_id=?", (proposal["id"],))
                    connection.execute("DELETE FROM analysis_fts WHERE proposal_id=?", (proposal["id"],))
                    connection.execute("UPDATE proposals SET analysis_state='idle' WHERE id=?", (proposal["id"],))
                if job_id:
                    self._item_status(job_id, proposal, "preparation", "completed", attempt=attempt)
                return {
                    "proposal_id": proposal["id"], "tdoc": proposal["tdoc"],
                    "cached": bool(metadata.get("cached")), "visual_count": visual_count,
                }
            except Exception as exc:
                if job_id:
                    self._item_status(job_id, proposal, "preparation", "failed", attempt=attempt, error=str(exc)[:500])
                self.database.execute(
                    "UPDATE proposals SET preparation_state='failed',preparation_error=? WHERE id=?",
                    (str(exc)[:500], proposal["id"]),
                )
                raise

    def _prepare_many(
        self,
        job_id: str,
        proposals: list[dict[str, Any]],
        *,
        progress_start: float = 0,
        progress_span: float = 1,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        successful: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        completed = 0
        cached_count = 0
        def update_status(message: str) -> None:
            current = self.database.one("SELECT progress FROM jobs WHERE id=?", (job_id,))
            self.database.update_job(job_id, progress=current["progress"] if current else progress_start, message=message)

        def prepare_with_retry(proposal: dict[str, Any]) -> dict[str, Any]:
            last_error: Exception | None = None
            for attempt in range(3):
                if self._is_cancelling(job_id):
                    raise RuntimeError("任务已取消")
                try:
                    return self._prepare_proposal(proposal, update_status, job_id, attempt)
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))
            raise last_error or RuntimeError("本地准备失败")

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="proposal-prepare") as pool:
            futures = {
                pool.submit(prepare_with_retry, proposal): proposal
                for proposal in proposals
            }
            for future in as_completed(futures):
                proposal = futures[future]
                if self._is_cancelling(job_id):
                    for pending in futures:
                        pending.cancel()
                    break
                try:
                    result = future.result()
                    successful.append(proposal)
                    cached_count += int(bool(result.get("cached")))
                except Exception as exc:
                    failed.append({"tdoc": proposal["tdoc"], "error": str(exc)})
                completed += 1
                self.database.update_job(
                    job_id,
                    progress=progress_start + progress_span * completed / max(len(proposals), 1),
                    message=(
                        f"本地准备 {completed}/{len(proposals)} · 当前 {proposal['tdoc']} · "
                        f"复用 {cached_count} · 失败 {len(failed)}"
                    ),
                )
        return successful, failed

    def _prepare_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        proposals = self._selected_proposals(payload)
        successful, failed = self._prepare_many(job_id, proposals)
        return {
            "completed_count": len(successful), "failed_count": len(failed),
            "successful_proposals": [item["tdoc"] for item in successful], "failed_proposals": failed,
        }

    def _analyze_prepared(
        self, proposal: dict[str, Any], job_id: str, position: int, total: int,
        model_option_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.database.one("SELECT * FROM analyses WHERE proposal_id=?", (proposal["id"],))
        prepared = self.database.one("SELECT * FROM preparations WHERE proposal_id=?", (proposal["id"],))
        if not prepared:
            raise ValueError("本地预处理结果不存在")
        profile, _ = resolve_model_option(model_option_id, self.database)
        has_vision = bool(profile and profile.get("api_key_encrypted") and profile.get("vision_model"))
        self._item_status(job_id, proposal, "model_analysis", "running")
        current_job = self.database.one("SELECT progress FROM jobs WHERE id=?", (job_id,))
        self.database.update_job(job_id, progress=current_job["progress"] if current_job else .4, message=f"正在分析 {proposal['tdoc']} 的文本与图表证据")
        self.database.execute("UPDATE proposals SET analysis_state='analyzing' WHERE id=?", (proposal["id"],))
        content = prepared["content"]
        visual_evidence = json.loads(prepared.get("visual_evidence_json") or "[]")
        unresolved = [item for item in visual_evidence if not item.get("resolved")]
        observations: list[str] = []
        resolved_by_vision = 0
        remaining_unresolved: list[dict[str, Any]] = []
        for visual_index, item in enumerate(unresolved, start=1):
            visual_path = Path(item.get("asset_path") or "")
            if not visual_path.exists() or not has_vision:
                remaining_unresolved.append({**item, "tdoc": proposal["tdoc"]})
                continue
            self.database.update_job(
                job_id,
                progress=0.45 + 0.35 * ((position - 1) + visual_index / max(len(unresolved), 1)) / max(total, 1),
                message=f"正在分析复杂图表 {visual_index}/{len(unresolved)} · {proposal['tdoc']}",
            )
            asset_sha = item.get("asset_sha256") or self._sha256(visual_path)
            model_key = f"{profile['id']}:{profile['vision_model']}"
            cached_visual = self.database.one(
                "SELECT result FROM visual_analysis_cache WHERE asset_sha256=? AND model_key=?",
                (asset_sha, model_key),
            )
            try:
                if cached_visual:
                    observation = cached_visual["result"]
                else:
                    with self._vision_slots:
                        observation = call_vision_model(
                            visual_path,
                            f"TDoc {proposal['tdoc']}; title: {proposal['title']}; visual {visual_index}",
                            self.database, model_option_id,
                        )
                if observation:
                    observations.append(f"[Visual {visual_index}: {visual_path.name}]\n{observation}")
                    resolved_by_vision += 1
                    if not cached_visual:
                        self.database.execute(
                            "INSERT OR REPLACE INTO visual_analysis_cache(asset_sha256,model_key,result,updated_at) VALUES(?,?,?,?)",
                            (asset_sha, model_key, observation, utc_now()),
                        )
                else:
                    remaining_unresolved.append({**item, "tdoc": proposal["tdoc"]})
            except Exception as exc:
                remaining_unresolved.append({**item, "tdoc": proposal["tdoc"], "unresolved_reason": str(exc)})
        if observations:
            content += "\n\n" + "\n\n".join(observations)
        model_key = f"{model_option_id}@{profile.get('_option_revision', 1)}" if model_option_id and profile else (f"{profile['id']}:{profile['text_model']}" if profile else "local")
        cached_summary = self.database.one(
            """SELECT summary_json FROM analysis_summaries WHERE proposal_id=? AND model_option_id=?
               AND source_sha256=? AND prompt_version='v2'""",
            (proposal["id"], model_key, prepared["source_sha256"]),
        )
        if cached_summary:
            summary = json.loads(cached_summary["summary_json"])
        else:
            summary = summarize_with_model(
                content, proposal["title"], proposal["tdoc"], self.database, model_option_id,
                cancelled=lambda: self._is_cancelling(job_id),
            )
            self.database.execute(
                """INSERT OR REPLACE INTO analysis_summaries(proposal_id,model_option_id,source_sha256,
                   prompt_version,summary_json,updated_at) VALUES(?,?,?,?,?,?)""",
                (proposal["id"], model_key, prepared["source_sha256"], "v2", json.dumps(summary, ensure_ascii=False), utc_now()),
            )
        citations = json.loads(prepared["citations_json"])
        coverage = {
            "resolved_local": int(prepared.get("resolved_visual_count") or 0),
            "resolved_vision": resolved_by_vision,
            "unresolved": remaining_unresolved,
        }
        vision_complete = not remaining_unresolved
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO analyses(proposal_id,content,summary_json,citations_json,visual_count,vision_complete,
                   visual_coverage_json,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(proposal_id) DO UPDATE SET content=excluded.content,
                   summary_json=excluded.summary_json,citations_json=excluded.citations_json,
                   visual_count=excluded.visual_count,vision_complete=excluded.vision_complete,
                   visual_coverage_json=excluded.visual_coverage_json,updated_at=excluded.updated_at""",
                (
                    proposal["id"], content, json.dumps(summary, ensure_ascii=False),
                    json.dumps(citations, ensure_ascii=False), prepared["visual_count"], int(vision_complete),
                    json.dumps(coverage, ensure_ascii=False), utc_now(),
                ),
            )
            connection.execute("DELETE FROM analysis_fts WHERE proposal_id=?", (proposal["id"],))
            connection.execute(
                "INSERT INTO analysis_fts(proposal_id,tdoc,title,content) VALUES(?,?,?,?)",
                (proposal["id"], proposal["tdoc"], proposal["title"], content),
            )
            connection.execute("UPDATE proposals SET analysis_state='analyzed' WHERE id=?", (proposal["id"],))
        self._item_status(job_id, proposal, "model_analysis", "completed")
        return {"proposal": proposal, "cached": bool(cached_summary), "vision_complete": vision_complete, "visual_coverage": coverage}

    def _analysis_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        proposals = self._selected_proposals(payload)
        prepared, failed = self._prepare_many(job_id, proposals, progress_span=0.45)
        completed: list[str] = []
        for index, proposal in enumerate(prepared, start=1):
            try:
                self._analyze_prepared(proposal, job_id, index, len(prepared))
                completed.append(proposal["tdoc"])
            except Exception as exc:
                failed.append({"tdoc": proposal["tdoc"], "error": str(exc)})
                self.database.execute("UPDATE proposals SET analysis_state='failed' WHERE id=?", (proposal["id"],))
        return {"completed_count": len(completed), "successful_proposals": completed, "failed_proposals": failed}

    def _begin_chat(self, job_id: str, payload: dict[str, Any], proposals: list[dict[str, Any]]) -> tuple[str, str]:
        thread_id = payload.get("thread_id") or uuid.uuid4().hex
        now = utc_now()
        if payload.get("assistant_message_id") and self.database.one("SELECT id FROM chat_messages WHERE id=?", (payload["assistant_message_id"],)):
            if proposals:
                self.database.execute(
                    "UPDATE chat_threads SET active_job_id=?,proposal_ids_json=?,updated_at=? WHERE id=?",
                    (job_id, json.dumps([p["id"] for p in proposals]), now, thread_id),
                )
            else:
                self.database.execute("UPDATE chat_threads SET active_job_id=?,updated_at=? WHERE id=?", (job_id, now, thread_id))
            return thread_id, payload["assistant_message_id"]
        if not self.database.one("SELECT id FROM chat_threads WHERE id=?", (thread_id,)):
            self.database.execute(
                """INSERT INTO chat_threads(id,meeting_id,proposal_ids_json,title,default_model_option_id,
                   summary,archived,active_job_id,created_at,updated_at) VALUES(?,?,?,?,?,'',0,?,?,?)""",
                (thread_id, payload["meeting_id"], json.dumps([p["id"] for p in proposals]),
                 payload["question"][:80], payload.get("model_option_id"), job_id, now, now),
            )
        else:
            self.database.execute(
                "UPDATE chat_threads SET active_job_id=?,default_model_option_id=COALESCE(?,default_model_option_id),proposal_ids_json=?,updated_at=? WHERE id=?",
                (job_id, payload.get("model_option_id"), json.dumps([p["id"] for p in proposals]), now, thread_id),
            )
            self.database.execute(
                "UPDATE chat_threads SET title=? WHERE id=? AND title='新会话'",
                (payload["question"][:80], thread_id),
            )
        sequence = int((self.database.one("SELECT COALESCE(MAX(sequence),0) AS value FROM chat_messages WHERE thread_id=?", (thread_id,)) or {"value": 0})["value"])
        self.database.execute(
            """INSERT INTO chat_messages(id,thread_id,role,content,citations_json,artifacts_json,
               proposal_ids_json,model_option_id,status,token_usage_json,sequence,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,'{}',?,?)""",
            (uuid.uuid4().hex, thread_id, "user", payload["question"], "[]", "[]",
             json.dumps([p["id"] for p in proposals]), payload.get("model_option_id"), "completed", sequence + 1, now),
        )
        assistant_id = uuid.uuid4().hex
        self.database.execute(
            """INSERT INTO chat_messages(id,thread_id,role,content,citations_json,artifacts_json,
               proposal_ids_json,model_option_id,status,token_usage_json,sequence,created_at)
               VALUES(?,?,?,'','[]','[]',?,?,?,'{}',?,?)""",
            (assistant_id, thread_id, "assistant", json.dumps([p["id"] for p in proposals]),
             payload.get("model_option_id"), "streaming", sequence + 2, now),
        )
        return thread_id, assistant_id

    @staticmethod
    def _local_chat_mode(question: str) -> str | None:
        value = question.strip().casefold()
        proposal_markers = (
            "提案", "文稿", "tdoc", "agenda", "公司观点", "上述分析", "上述结论",
            "风险", "争议", "逐篇", "全面分析", "3gpp", "报告", "word", "ppt",
            "按公司", "主要观点", "核心问题", "归纳各公司", "总结观点", "技术变化", "差异",
            "proposal", "report", "powerpoint",
            "what changes", "what changed", "differences between",
        )
        general_markers = (
            "你好", "hello", "hi ", "翻译", "translate", "改写", "润色", "写一封",
            "解释一下", "是什么", "天气", "编程", "代码", "数学",
        )
        if any(marker in value for marker in proposal_markers):
            return "proposal"
        if any(marker in value for marker in general_markers) or len(value) <= 8:
            return "general"
        return None

    def _resolve_chat_mode(self, payload: dict[str, Any]) -> str:
        if payload.get("resolved_mode") in {"proposal", "general"}:
            return payload["resolved_mode"]
        requested = payload.get("chat_mode", "auto")
        if requested in {"proposal", "general"}:
            return requested
        local = self._local_chat_mode(payload["question"])
        if local:
            return local
        try:
            classification, _, _ = stream_text_model(
                [
                    {"role": "system", "content": "Classify the request. Reply only proposal or general. proposal means it needs selected 3GPP documents, prior proposal analysis, comparison, risk, or report generation."},
                    {"role": "user", "content": payload["question"][:1000]},
                ],
                self.database,
                model_option_id=payload.get("model_option_id"),
                max_tokens=4,
                timeout_seconds=2,
                max_attempts=1,
            )
            return "proposal" if "proposal" in classification.casefold() else "general"
        except Exception:
            return "general"

    def _general_chat_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        thread_id, assistant_id = self._begin_chat(job_id, payload, [])
        history = self.database.query(
            """SELECT role,content FROM chat_messages WHERE thread_id=? AND id<>?
               AND status IN ('completed','streaming') ORDER BY created_at DESC LIMIT 13""",
            (thread_id, assistant_id),
        )
        conversation = list(reversed(history))
        if conversation and conversation[-1]["role"] == "user" and conversation[-1]["content"] == payload["question"]:
            conversation.pop()
        self.database.update_job(job_id, progress=0.12, message="正在理解问题并组织回答")
        answer_parts: list[str] = []
        last_flush = time.monotonic()
        flushed_length = 0

        def on_delta(delta: str) -> None:
            nonlocal last_flush, flushed_length
            if self._is_cancelling(job_id):
                return
            answer_parts.append(delta)
            self.database.add_job_event(job_id, "answer_delta", {"delta": delta, "message_id": assistant_id, "thread_id": thread_id})
            current = "".join(answer_parts)
            if len(current) - flushed_length >= 1024 or time.monotonic() - last_flush >= 0.5:
                self.database.execute("UPDATE chat_messages SET content=? WHERE id=? AND status='streaming'", (current, assistant_id))
                self.database.update_job(job_id, partial_result={"answer": current, "thread_id": thread_id, "message_id": assistant_id})
                last_flush, flushed_length = time.monotonic(), len(current)

        generated, finish_reason, usage = stream_text_model(
            [
                {"role": "system", "content": "你是3GPP提案工作台中的通用助手。用用户使用的语言直接、准确地回答。除非用户明确提到提案，否则不要假装正在分析文稿。"},
                *[{"role": item["role"], "content": item["content"]} for item in conversation if item["content"]],
                {"role": "user", "content": payload["question"]},
            ],
            self.database,
            model_option_id=payload.get("model_option_id"),
            on_delta=on_delta,
            cancelled=lambda: self._is_cancelling(job_id),
            continuations=2,
        )
        if not generated and finish_reason == "unavailable":
            raise ValueError("尚未配置可用的文本模型，请先在设置中配置模型")
        answer = "".join(answer_parts)
        status = "cancelled" if finish_reason == "cancelled" else "completed"
        self.database.execute(
            """UPDATE chat_messages SET content=?,status=?,finish_reason=?,token_usage_json=?
               WHERE id=? AND status='streaming'""",
            (answer, status, finish_reason, json.dumps(usage, ensure_ascii=False), assistant_id),
        )
        return {
            "thread_id": thread_id, "message_id": assistant_id, "answer": answer,
            "citations": [], "resolved_mode": "general", "finish_reason": finish_reason,
            "model_option_id": payload.get("model_option_id"),
        }

    def _answer_question(
        self,
        job_id: str,
        payload: dict[str, Any],
        successful: list[dict[str, Any]],
        failed: list[dict[str, str]],
        unresolved_visuals: list[dict[str, Any]],
        thread_id: str,
        assistant_id: str,
        total_selected: int,
    ) -> dict[str, Any]:
        ids = [proposal["id"] for proposal in successful]
        placeholders = ",".join("?" for _ in ids)
        rows = self.database.query(
            f"""SELECT p.id,p.tdoc,p.title,a.content,a.summary_json,a.citations_json
                FROM proposals p JOIN analyses a ON a.proposal_id=p.id
                WHERE p.meeting_id=? AND p.id IN ({placeholders}) ORDER BY p.agenda_sort,p.tdoc""",
            tuple([payload["meeting_id"], *ids]),
        )
        all_ids = payload.get("proposal_ids") or ids
        all_placeholders = ",".join("?" for _ in all_ids)
        all_rows = self.database.query(
            f"SELECT tdoc,title,source,agenda_item FROM proposals WHERE id IN ({all_placeholders}) ORDER BY agenda_sort,tdoc",
            tuple(all_ids),
        ) if all_ids else []
        selection_overview = "\n".join(f"{item['tdoc']} | {item['agenda_item']} | {item['source']} | {item['title']}" for item in all_rows)
        evidence: list[str] = []
        citations: list[dict[str, Any]] = []
        context_chars = 160000
        per_proposal = max(1800, min(7000, context_chars // max(len(rows), 1)))
        for row in rows:
            evidence.append(
                f"[{row['tdoc']}] {row['title']}\n结构化摘要：{row['summary_json']}\n正文证据：{(row.get('content') or '')[:per_proposal]}"
            )
            stored = json.loads(row["citations_json"] or "[]")
            citations.append({
                "tdoc": row["tdoc"], "title": row["title"], "proposal_id": row["id"],
                "artifact_name": stored[0]["artifact_name"] if stored else "", "page_or_slide": 1,
            })
        notices: list[str] = []
        if len(successful) < total_selected:
            notices.append(f"本次采用自适应分析，重点分析 {len(successful)}/{total_selected} 篇提案；如需逐篇覆盖，可选择全面分析。")
        if failed:
            notices.append(
                f"处理范围不完整：成功 {len(successful)} 篇，失败 {len(failed)} 篇（"
                + "、".join(item["tdoc"] for item in failed) + "）。"
            )
        if unresolved_visuals:
            references = "、".join(
                f"{item.get('tdoc', '')}/{item.get('artifact', '图表')}" for item in unresolved_visuals[:6]
            )
            notices.append(
                f"有 {len(unresolved_visuals)} 个复杂图表无法在本地完整解析，相关结论可能不完整。涉及：{references}。"
            )
        history = self.database.query(
            """SELECT role,content FROM chat_messages WHERE thread_id=? AND status='completed'
               AND id<>? ORDER BY created_at DESC LIMIT 12""", (thread_id, assistant_id),
        )
        conversation = list(reversed(history))
        if conversation and conversation[-1]["role"] == "user" and conversation[-1]["content"] == payload["question"]:
            conversation.pop()
        prefix = ("\n".join(notices) + "\n\n") if notices else ""
        answer_parts = [prefix]
        if prefix:
            self.database.add_job_event(job_id, "answer_delta", {"delta": prefix, "message_id": assistant_id})
        last_flush = time.monotonic()
        flushed_length = len(prefix)

        def on_delta(delta: str) -> None:
            nonlocal last_flush, flushed_length
            answer_parts.append(delta)
            self.database.add_job_event(job_id, "answer_delta", {"delta": delta, "message_id": assistant_id})
            current = "".join(answer_parts)
            if len(current) - flushed_length >= 1024 or time.monotonic() - last_flush >= 0.5:
                self.database.execute("UPDATE chat_messages SET content=? WHERE id=?", (current, assistant_id))
                self.database.update_job(job_id, partial_result={"answer": current, "thread_id": thread_id, "message_id": assistant_id})
                last_flush, flushed_length = time.monotonic(), len(current)

        model_messages = [
            {"role": "system", "content": "请使用中文回答，仅依据提供的3GPP提案证据。结合会话上下文理解追问，但不要把用户陈述当作规范事实。每个关键结论标注TDoc；证据不足时明确说明。"},
            *[{"role": item["role"], "content": item["content"]} for item in conversation if item["content"]],
            {"role": "user", "content": f"当前问题：{payload['question']}\n\n全部选择范围：\n{selection_overview}\n\n重点提案证据：\n" + "\n\n".join(evidence)},
        ]
        generated, finish_reason, usage = stream_text_model(
            model_messages, self.database, model_option_id=payload.get("model_option_id"), on_delta=on_delta,
            cancelled=lambda: self._is_cancelling(job_id), continuations=2,
        )
        if not generated:
            fallback = "尚未配置可用的文本模型。请在设置中配置模型后重试。"
            on_delta(fallback)
            finish_reason = "unavailable"
        answer = "".join(answer_parts)
        message_status = "cancelled" if finish_reason == "cancelled" else "completed"
        self.database.execute(
            """UPDATE chat_messages SET content=?,citations_json=?,status=?,finish_reason=?,token_usage_json=? WHERE id=?""",
            (answer, json.dumps(citations[:20], ensure_ascii=False), message_status, finish_reason,
             json.dumps(usage, ensure_ascii=False), assistant_id),
        )
        for citation in citations[:20]:
            self.database.add_job_event(job_id, "citation", citation)
        return {"thread_id": thread_id, "answer": answer, "citations": citations[:20],
                "finish_reason": finish_reason, "model_option_id": payload.get("model_option_id"),
                "analysis_coverage": {"analyzed": len(successful), "selected": total_selected}}

    @staticmethod
    def _is_report_request(question: str) -> bool:
        value = question.strip().casefold()
        if value.startswith(("/report", "/word", "/ppt")):
            return True
        action = r"(?:生成|制作|创建|输出|导出|撰写|generate|create|make|export|write)"
        artifact = r"(?:报告|文档|汇报|ppt|powerpoint|word|docx|report|deck|presentation)"
        return bool(re.search(action + r".{0,20}" + artifact + "|" + artifact + r".{0,20}" + action, value, re.I))

    def _report_outline_response(
        self, payload: dict[str, Any], proposals: list[dict[str, Any]], failed: list[dict[str, str]],
        unresolved_visuals: list[dict[str, Any]], thread_id: str, assistant_id: str,
    ) -> dict[str, Any]:
        options = dict(payload.get("report_options") or {})
        question = payload["question"]
        lowered = question.casefold()
        if any(token in lowered for token in ("ppt", "powerpoint", "presentation", "/ppt")):
            options["format"] = "pptx"
        elif any(token in lowered for token in ("word", "docx", "/word")):
            options["format"] = "docx"
        if "中文" in question or "chinese" in lowered:
            options["language"] = "zh"
        format_name = options.get("format", "docx")
        template_id = options.get("template_id")
        warning = ""
        if template_id:
            template = self.database.one("SELECT kind,name FROM templates WHERE id=?", (template_id,))
            if not template or template["kind"] != format_name:
                warning = "所选模板与报告格式不一致，本次大纲将使用默认模板。"
                template_id = None
        title = options.get("title") or ("3GPP 提案分析报告" if options.get("language") == "zh" else "3GPP Proposal Analysis")
        history = self.database.query(
            "SELECT id,role,content,citations_json FROM chat_messages WHERE thread_id=? AND id<>? AND status='completed' ORDER BY created_at",
            (thread_id, assistant_id),
        )
        thread = self.database.one("SELECT summary FROM chat_threads WHERE id=?", (thread_id,)) or {}
        conversation_context = {
            "rolling_summary": thread.get("summary", ""),
            "recent_messages": [{"role": item["role"], "content": item["content"], "citations": json.loads(item["citations_json"] or "[]")} for item in history[-12:]],
        }
        report = create_outline({
            "meeting_id": payload["meeting_id"], "proposal_ids": [item["id"] for item in proposals],
            "format": format_name, "title": title, "language": options.get("language", "en"),
            "template_id": template_id, "template_mode": options.get("template_mode", "strict"),
            "instruction": question, "source_thread_id": thread_id,
            "source_message_ids": [item["id"] for item in history], "conversation_context": conversation_context,
        }, self.database)
        artifact = {
            "kind": "report_outline", "report_id": report["id"], "format": format_name,
            "title": title, "outline": report["outline"], "proposal_count": report["proposal_count"],
            "template_id": template_id, "warning": warning, "message_count": len(history),
        }
        content = (warning + "\n\n" if warning else "") + "报告大纲已基于本会话历史和提案证据生成。确认内容后即可生成文件。"
        self.database.execute(
            "UPDATE chat_messages SET content=?,artifacts_json=?,status='completed',finish_reason='stop' WHERE id=?",
            (content, json.dumps([artifact], ensure_ascii=False), assistant_id),
        )
        return {"thread_id": thread_id, "answer": content, "citations": [], "artifacts": [artifact],
                "failed_proposals": failed, "visual_coverage": {"unresolved": unresolved_visuals}}

    def _chat_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        resolved_mode = self._resolve_chat_mode(payload)
        if resolved_mode == "general":
            return self._general_chat_job(job_id, payload)
        report_request = self._is_report_request(payload["question"])
        if not payload.get("proposal_ids") and report_request and payload.get("thread_id"):
            thread_scope = self.database.one("SELECT proposal_ids_json FROM chat_threads WHERE id=?", (payload["thread_id"],))
            payload["proposal_ids"] = json.loads(thread_scope["proposal_ids_json"] or "[]") if thread_scope else []
        proposals = self._selected_proposals(payload)
        thread_id, assistant_id = self._begin_chat(job_id, payload, proposals)
        explicit_exhaustive = payload.get("analysis_mode") == "exhaustive" or report_request or bool(re.search(r"全面|全部|逐篇|每篇|all\b|every\b", payload["question"], re.I))
        scope = self._adaptive_scope(proposals, payload["question"], explicit_exhaustive)
        _, intent_message = self._intent(payload["question"], report_request)
        self.database.update_job(job_id, progress=0.01, message=f"{intent_message}，正在检查 {len(scope)} 篇重点提案")
        prepared, failed = self._prepare_many(job_id, scope, progress_start=0.02, progress_span=0.38)
        analyzed: list[dict[str, Any]] = []
        unresolved_visuals: list[dict[str, Any]] = []
        profile, _ = resolve_model_option(payload.get("model_option_id"), self.database)
        concurrency = min(6, max(1, int(profile.get("max_concurrency") or 4))) if profile else 4
        pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="proposal-summary")
        try:
            futures = {
                pool.submit(self._analyze_prepared, proposal, job_id, index, len(prepared), payload.get("model_option_id")): proposal
                for index, proposal in enumerate(prepared, start=1)
            }
            completed = 0
            for future in as_completed(futures):
                proposal = futures[future]
                if self._is_cancelling(job_id):
                    for pending in futures:
                        pending.cancel()
                    break
                try:
                    result = future.result()
                    analyzed.append(proposal)
                    unresolved_visuals.extend(result.get("visual_coverage", {}).get("unresolved", []))
                except Exception as exc:
                    failed.append({"tdoc": proposal["tdoc"], "error": str(exc)})
                    self._item_status(job_id, proposal, "model_analysis", "failed", error=str(exc)[:500])
                    self.database.execute("UPDATE proposals SET analysis_state='failed' WHERE id=?", (proposal["id"],))
                completed += 1
                self.database.update_job(
                    job_id, progress=0.4 + 0.45 * completed / max(len(prepared), 1),
                    message=f"{intent_message}：已处理 {completed}/{len(prepared)}，失败 {len(failed)}",
                )
        finally:
            pool.shutdown(wait=not self._is_cancelling(job_id), cancel_futures=True)
        if self._is_cancelling(job_id):
            self.database.execute("UPDATE chat_messages SET status='cancelled',finish_reason='cancelled' WHERE id=?", (assistant_id,))
            return {"thread_id": thread_id, "answer": "", "cancelled": True, "successful_proposals": [p["tdoc"] for p in analyzed]}
        if not analyzed:
            raise ValueError("所选提案全部处理失败：" + "；".join(f"{item['tdoc']} {item['error']}" for item in failed))
        question_excerpt = payload["question"].replace("\n", " ")[:32]
        self.database.update_job(job_id, progress=0.9, message=f"正在组织证据并回答“{question_excerpt}”")
        answer = (
            self._report_outline_response(payload, analyzed, failed, unresolved_visuals, thread_id, assistant_id)
            if report_request
            else self._answer_question(job_id, payload, analyzed, failed, unresolved_visuals, thread_id, assistant_id, len(proposals))
        )
        answer["message_id"] = assistant_id
        for artifact in answer.get("artifacts", []):
            self.database.add_job_event(job_id, "artifact", artifact)
        messages = self.database.query("SELECT role,content FROM chat_messages WHERE thread_id=? AND status='completed' ORDER BY created_at", (thread_id,))
        if len(messages) > 12:
            rolling = "\n".join(f"{item['role']}: {item['content'][:1200]}" for item in messages[:-12])[-16000:]
            self.database.execute("UPDATE chat_threads SET summary=?,proposal_ids_json=?,updated_at=? WHERE id=?", (rolling, json.dumps([p["id"] for p in proposals]), utc_now(), thread_id))
        return {
            **answer,
            "resolved_mode": "proposal",
            "successful_proposals": [proposal["tdoc"] for proposal in analyzed],
            "failed_proposals": failed,
            "vision_complete": not unresolved_visuals,
            "visual_coverage": {"unresolved": unresolved_visuals},
            "analysis_coverage": {"analyzed": len(analyzed), "selected": len(proposals), "mode": "exhaustive" if explicit_exhaustive else "adaptive"},
        }

    def _report_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.database.update_job(job_id, progress=0.15, message="正在应用模板并生成报告")
        result = render_report(payload["report_id"], payload["outline"], self.database)
        self.database.update_job(job_id, progress=0.9, message="正在完成质量检查和预览")
        report = self.database.one("SELECT * FROM reports WHERE id=?", (payload["report_id"],)) or {}
        template = self.database.one("SELECT name FROM templates WHERE id=?", (report.get("template_id"),)) if report.get("template_id") else None
        proposal_count = len(json.loads(report.get("proposal_ids_json") or "[]"))
        artifact = {
            "kind": "report_file", "report_id": payload["report_id"], "format": report.get("format"),
            "title": report.get("title"), "file_name": result["file_name"],
            "proposal_count": proposal_count, "template_name": template["name"] if template else "默认模板",
            "preview_available": result["preview_available"], "quality": result["quality"],
            "download_url": f"/api/reports/{payload['report_id']}/file",
            "preview_url": f"/api/reports/{payload['report_id']}/preview" if result["preview_available"] else None,
        }
        if payload.get("thread_id"):
            sequence = int((self.database.one("SELECT COALESCE(MAX(sequence),0) AS value FROM chat_messages WHERE thread_id=?", (payload["thread_id"],)) or {"value": 0})["value"])
            self.database.execute(
                "INSERT INTO chat_messages(id,thread_id,role,content,citations_json,artifacts_json,sequence,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, payload["thread_id"], "assistant", "报告文件已生成。", "[]",
                 json.dumps([artifact], ensure_ascii=False), sequence + 1, utc_now()),
            )
        return {**result, "thread_id": payload.get("thread_id"), "artifact": artifact}

    def _download_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        proposals = self._selected_proposals(payload)
        meeting = self.database.one("SELECT * FROM meetings WHERE id=?", (payload["meeting_id"],))
        package_name = f"3gpp-{meeting['name'] if meeting else 'meeting'}-{len(proposals)}-{int(time.time())}.zip"
        package_path = settings.packages_dir / package_name
        successful: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for index, proposal in enumerate(proposals):
            if self._is_cancelling(job_id):
                break
            self.database.update_job(
                job_id, progress=index / max(len(proposals), 1) * 0.85,
                message=f"正在下载 {proposal['tdoc']} ({index + 1}/{len(proposals)})",
            )
            try:
                path, metadata = self._ensure_source(proposal)
                successful.append({"proposal": proposal, "path": path, "sha256": self._sha256(path), **metadata})
            except Exception as exc:
                failed.append({"tdoc": proposal["tdoc"], "title": proposal["title"], "error": str(exc)})
        if not successful and not self._is_cancelling(job_id):
            raise ValueError("所有提案均下载失败")
        if self._is_cancelling(job_id):
            return {"cancelled": True}
        self.database.update_job(job_id, progress=0.9, message="正在生成总压缩包")
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
            for item in successful:
                proposal = item["proposal"]
                if (proposal.get("file_kind") or "zip") == "zip":
                    archive_name = f"proposals/{proposal.get('file_name') or proposal['tdoc'] + '.zip'}"
                else:
                    archive_name = f"documents/{proposal.get('file_name') or item['path'].name}"
                output.write(item["path"], archive_name)
            rows = [["TDoc", "Title", "Source", "Agenda", "Original URL", "Bytes", "SHA-256", "Status"]]
            for item in successful:
                proposal = item["proposal"]
                rows.append([
                    proposal["tdoc"], proposal["title"], proposal["source"], proposal["agenda_item"],
                    proposal.get("file_url") or proposal["zip_url"], str(item.get("bytes", item["path"].stat().st_size)), item["sha256"], "downloaded",
                ])
            output.writestr("manifest.csv", _csv_bytes(rows))
            if failed:
                output.writestr("failed.csv", _csv_bytes([["TDoc", "Title", "Error"], *[[x["tdoc"], x["title"], x["error"]] for x in failed]]))
        return {
            "file_path": str(package_path), "file_name": package_name,
            "completed_count": len(successful), "failed_count": len(failed),
            "actual_bytes": package_path.stat().st_size,
        }


def _csv_bytes(rows: list[list[str]]) -> bytes:
    import io

    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


runner = JobRunner()
