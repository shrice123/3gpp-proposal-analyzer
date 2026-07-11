from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
import zipfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROPOSAL_TOOL_DATA_DIR", tempfile.mkdtemp(prefix="proposal-test-data-"))

from openpyxl import Workbook
from fastapi.testclient import TestClient

from app.database import Database, _rebuild_model_registry, utc_now
from app.extractors import extract_office_visual_evidence, safe_extract
from app.jobs import JobRunner
from app.models import model_options, save_profile, stream_text_model
from app.reports import create_outline, render_report
from app.threegpp import list_proposals, parse_index, primary_source, validate_3gpp_url
from app.source_normalization import apply_meeting_parent_names, canonical_source_name, extract_primary_source, save_source_alias
from app.main import app


class UrlValidationTests(unittest.TestCase):
    def test_accepts_3gpp_docs_directory(self):
        result = validate_3gpp_url(
            "https://www.3gpp.org/ftp/tsg_sa/WG2_Arch/TSGS2_173_Goa_2026-02/Docs"
        )
        self.assertTrue(result.endswith("/"))

    def test_rejects_non_3gpp_host(self):
        with self.assertRaises(ValueError):
            validate_3gpp_url("https://example.com/ftp/meeting/Docs")


class ApiSmokeTests(unittest.TestCase):
    def test_health_and_runtime_status(self):
        with TestClient(app) as client:
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertTrue(health.json()["ok"])
            runtime = client.get("/api/runtime/status")
            self.assertEqual(runtime.status_code, 200)
            self.assertIn("components", runtime.json())

    def test_migration_endpoints_are_removed(self):
        with TestClient(app) as client:
            paths = client.get("/api/openapi.json").json()["paths"]
            self.assertFalse(any(path.startswith("/api/migrations") for path in paths))

    def test_chat_session_lifecycle(self):
        with TestClient(app) as client:
            created = client.post("/api/chats", json={"meeting_id": "session-test", "title": "New", "proposal_ids": []})
            self.assertEqual(created.status_code, 200)
            thread_id = created.json()["id"]
            self.assertTrue(any(item["id"] == thread_id for item in client.get("/api/chats?meeting_id=session-test").json()))
            self.assertEqual(client.patch(f"/api/chats/{thread_id}", json={"archived": True}).status_code, 200)
            self.assertEqual(client.delete(f"/api/chats/{thread_id}").status_code, 200)

    def test_archived_sessions_are_separate_and_batch_deletable(self):
        with TestClient(app) as client:
            ids = []
            for title in ("Archive A", "Archive B"):
                thread = client.post("/api/chats", json={"meeting_id": "archive-test", "title": title, "proposal_ids": []}).json()
                ids.append(thread["id"])
                client.patch(f"/api/chats/{thread['id']}", json={"archived": True})
            active_ids = {item["id"] for item in client.get("/api/chats?state=active").json()}
            archived_ids = {item["id"] for item in client.get("/api/chats?state=archived").json()}
            self.assertTrue(set(ids).isdisjoint(active_ids))
            self.assertTrue(set(ids).issubset(archived_ids))
            deleted = client.post("/api/chats/batch-delete", json={"thread_ids": ids})
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(deleted.json()["deleted"], 2)

    def test_chat_submission_persists_messages_and_is_idempotent(self):
        with TestClient(app) as client:
            thread = client.post("/api/chats", json={"meeting_id": "atomic-chat", "title": "Atomic", "proposal_ids": []}).json()
            payload = {"meeting_id": "atomic-chat", "proposal_ids": [], "question": "你好", "thread_id": thread["id"],
                       "chat_mode": "general", "client_request_id": "request-atomic-0001"}
            first = client.post("/api/chat-jobs", json=payload)
            second = client.post("/api/chat-jobs", json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["id"], second.json()["id"])
            messages = client.get(f"/api/chats/{thread['id']}/messages").json()
            self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
            self.assertEqual(messages[0]["content"], "你好")
            self.assertLess(messages[0]["sequence"], messages[1]["sequence"])

    def test_five_chat_sessions_complete_independently(self):
        def fake_stream(*args, **kwargs):
            kwargs["on_delta"]("独立回答")
            return "独立回答", "stop", {}
        with patch("app.jobs.stream_text_model", side_effect=fake_stream), TestClient(app) as client:
            jobs = []
            threads = []
            for index in range(5):
                thread = client.post("/api/chats", json={"meeting_id": "multi-chat", "title": f"Thread {index}", "proposal_ids": []}).json()
                threads.append(thread["id"])
                response = client.post("/api/chat-jobs", json={"meeting_id": "multi-chat", "proposal_ids": [], "question": f"你好 {index}",
                    "thread_id": thread["id"], "chat_mode": "general", "client_request_id": f"multi-request-{index:04d}"})
                self.assertEqual(response.status_code, 200)
                jobs.append(response.json()["id"])
            deadline = time.monotonic() + 5
            statuses = {}
            while time.monotonic() < deadline:
                statuses = {job: client.get(f"/api/jobs/{job}").json()["status"] for job in jobs}
                if all(status == "completed" for status in statuses.values()): break
                time.sleep(.05)
            self.assertTrue(all(status == "completed" for status in statuses.values()), statuses)
            for thread in threads:
                messages = client.get(f"/api/chats/{thread}/messages").json()
                self.assertEqual(messages[-1]["content"], "独立回答")


class DatabaseUpgradeTests(unittest.TestCase):
    def test_database_open_retries_and_pool_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            real_connect = sqlite3.connect
            attempts = 0
            def flaky_connect(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts <= 2:
                    raise sqlite3.OperationalError("unable to open database file")
                return real_connect(*args, **kwargs)
            with patch("app.database.sqlite3.connect", side_effect=flaky_connect):
                database = Database(Path(temp) / "retry.sqlite3")
            errors = []
            threads = [threading.Thread(target=lambda: database.one("SELECT 1 AS ok") or errors.append("missing")) for _ in range(24)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertFalse(errors)
            self.assertLessEqual(database.health()["pool_size"], 6)
    def test_schema_five_jobs_upgrade_adds_thread_id_before_index(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "schema5.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,progress REAL NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',payload_json TEXT NOT NULL,result_json TEXT,partial_result_json TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
                connection.execute("INSERT INTO jobs(id,kind,status,payload_json,created_at,updated_at) VALUES('j','chat','completed','{}','now','now')")
            database = Database(path)
            columns = {row["name"] for row in database.query("PRAGMA table_info(jobs)")}
            self.assertIn("thread_id", columns)
            self.assertEqual(database.one("SELECT value FROM app_meta WHERE key='schema_version'")["value"], "7")

    def test_schema_seven_backfills_stable_message_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "messages.sqlite3"
            database = Database(path)
            now = utc_now()
            database.execute("INSERT INTO chat_threads(id,meeting_id,proposal_ids_json,title,summary,created_at,updated_at) VALUES('t','m','[]','T','',?,?)", (now, now))
            database.execute("INSERT INTO chat_messages(id,thread_id,role,content,sequence,created_at) VALUES('u','t','user','question',1,?)", (now,))
            database.execute("INSERT INTO chat_messages(id,thread_id,role,content,sequence,created_at) VALUES('a','t','assistant','answer',2,?)", (now,))
            reopened = Database(path)
            rows = reopened.query("SELECT id,sequence FROM chat_messages WHERE thread_id='t' ORDER BY sequence")
            self.assertEqual(rows, [{"id": "u", "sequence": 1}, {"id": "a", "sequence": 2}])

    def test_backfills_primary_source_for_existing_database(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """CREATE TABLE proposals (
                       id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, tdoc TEXT NOT NULL, title TEXT NOT NULL,
                       source TEXT NOT NULL, agenda_item TEXT NOT NULL, agenda_description TEXT NOT NULL,
                       agenda_sort REAL NOT NULL DEFAULT 999999, status TEXT NOT NULL DEFAULT '',
                       abstract TEXT NOT NULL DEFAULT '', zip_url TEXT NOT NULL,
                       download_state TEXT NOT NULL DEFAULT 'idle', analysis_state TEXT NOT NULL DEFAULT 'idle',
                       UNIQUE(meeting_id,tdoc))"""
                )
                connection.execute(
                    """INSERT INTO proposals(id,meeting_id,tdoc,title,source,agenda_item,agenda_description,zip_url)
                       VALUES('p','m','S2-1','Title','Huawei, HiSilicon','20.1','Agenda','https://example.invalid/p.zip')"""
                )
            database = Database(path)
            proposal = database.one("SELECT * FROM proposals WHERE id='p'")
            self.assertEqual(proposal["primary_source"], "Huawei")
            self.assertEqual(proposal["canonical_source"], "Huawei")
            self.assertEqual(proposal["preparation_state"], "idle")

    def test_schema_five_job_events_and_model_options(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "v5.sqlite3")
            job_id = database.create_job("chat", {"question": "test"})
            database.update_job(job_id, progress=.5, message="streaming", partial_result={"answer": "part"})
            self.assertGreaterEqual(len(database.query("SELECT * FROM job_events WHERE job_id=?", (job_id,))), 2)
            save_profile({"name": "DeepSeek", "provider": "deepseek", "base_url": "https://api.deepseek.com/v1", "api_key": "test", "text_model": "deepseek-v4-flash", "vision_model": "", "embedding_model": "", "available_models": ["deepseek-v4-pro"], "deployment": "external", "max_output_tokens": 8192, "context_window": 65536, "max_concurrency": 4, "request_timeout_seconds": 180, "enabled": True, "is_default": True}, database)
            options = model_options(database)
            self.assertEqual({item["model"] for item in options}, {"deepseek-v4-flash", "deepseek-v4-pro"})

            class FakeResponse:
                status_code = 200
                def __enter__(self): return self
                def __exit__(self, *_): return False
                def raise_for_status(self): return None
                def iter_lines(self):
                    return iter(['data: {"choices":[{"delta":{"content":"part"},"finish_reason":"length"}]}', 'data: [DONE]'])
            deltas = []
            with patch("app.models.httpx.stream", return_value=FakeResponse()):
                text, reason, _ = stream_text_model([{"role": "user", "content": "long"}], database, model_option_id=options[0]["id"], on_delta=deltas.append, continuations=1)
            self.assertEqual(text, "partpart")
            self.assertEqual(reason, "length")
            self.assertEqual(deltas, ["part", "part"])

            class FakeNonStream:
                def raise_for_status(self): return None
                def json(self): return {"choices": [{"message": {"content": "fallback answer"}, "finish_reason": "stop"}]}
            with patch("app.models.httpx.stream", side_effect=RuntimeError("stream unsupported")), patch("app.models.httpx.post", return_value=FakeNonStream()):
                text, reason, _ = stream_text_model([{"role": "user", "content": "fallback"}], database, model_option_id=options[0]["id"], max_attempts=1)
            self.assertEqual(text, "fallback answer")
            self.assertEqual(reason, "stop")

    def test_model_registry_globally_deduplicates_and_rebinds_stably(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "models.sqlite3")
            base = {"provider": "custom", "base_url": "http://localhost/v1", "api_key": "key", "vision_model": "", "embedding_model": "", "available_models": [], "deployment": "local", "max_output_tokens": 8192, "context_window": 65536, "max_concurrency": 4, "request_timeout_seconds": 180, "enabled": True, "is_default": False}
            first = save_profile({**base, "name": "First", "text_model": "DeepSeek-V4-Flash"}, database)
            second = save_profile({**base, "name": "Second", "text_model": "  deepseek-v4-flash  "}, database)
            options = model_options(database)
            self.assertEqual(len(options), 1)
            stable_id = options[0]["id"]
            database.execute("UPDATE model_profiles SET last_test_ok=1,last_tested_at=? WHERE id=?", ("2099-01-01T00:00:00+00:00", first["id"]))
            with database.transaction() as connection:
                _rebuild_model_registry(connection)
            rebound = database.one("SELECT * FROM model_options WHERE id=?", (stable_id,))
            self.assertEqual(rebound["profile_id"], first["id"])
            self.assertGreaterEqual(rebound["revision"], 2)
            self.assertNotEqual(first["id"], second["id"])


class ChatRoutingAndCancellationTests(unittest.TestCase):
    def test_company_summary_routes_to_proposal_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            runner = JobRunner(Database(Path(temp) / "route.sqlite3"))
            self.assertEqual(runner._resolve_chat_mode({"chat_mode": "auto", "question": "按公司总结主要观点", "proposal_ids": ["p"]}), "proposal")
            self.assertEqual(runner._resolve_chat_mode({"chat_mode": "auto", "question": "你好", "proposal_ids": []}), "general")

    def test_general_mode_does_not_prepare_proposals(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "general.sqlite3")
            now = utc_now()
            database.execute("INSERT INTO model_profiles(id,name,provider,base_url,text_model,api_key_encrypted,available_models_json,deployment,enabled,is_default,updated_at) VALUES('p','Local','custom','http://local/v1','model','plain:key','[]','local',1,1,?)", (now,))
            with database.transaction() as connection:
                _rebuild_model_registry(connection)
            option = model_options(database)[0]["id"]
            runner = JobRunner(database)
            job_id = database.create_job("chat", {"meeting_id": "m", "proposal_ids": [], "question": "你好", "chat_mode": "general"})
            with patch("app.jobs.stream_text_model", side_effect=lambda *args, **kwargs: (kwargs["on_delta"]("你好，我可以帮你。") or "你好，我可以帮你。", "stop", {})), patch.object(runner, "_prepare_many") as prepare:
                result = runner._chat_job(job_id, {"meeting_id": "m", "proposal_ids": [], "question": "你好", "chat_mode": "general", "model_option_id": option})
            prepare.assert_not_called()
            self.assertEqual(result["resolved_mode"], "general")
            self.assertIn("你好", result["answer"])

    def test_cancel_immediately_unlocks_only_target_thread(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "cancel.sqlite3")
            now = utc_now()
            for thread in ("a", "b"):
                database.execute("INSERT INTO chat_threads(id,meeting_id,proposal_ids_json,title,summary,archived,created_at,updated_at) VALUES(?,?,?,'Thread','',0,?,?)", (thread, "m", "[]", now, now))
            first_job = database.create_job("chat", {"thread_id": "a", "meeting_id": "m", "proposal_ids": []})
            second_job = database.create_job("chat", {"thread_id": "b", "meeting_id": "m", "proposal_ids": []})
            database.execute("UPDATE jobs SET status='running' WHERE id IN (?,?)", (first_job, second_job))
            database.execute("UPDATE chat_threads SET active_job_id=? WHERE id='a'", (first_job,))
            database.execute("UPDATE chat_threads SET active_job_id=? WHERE id='b'", (second_job,))
            database.execute("INSERT INTO chat_messages(id,thread_id,role,content,status,created_at) VALUES('ma','a','assistant','','streaming',?)", (now,))
            runner = JobRunner(database)
            runner.cancel(first_job)
            self.assertEqual(database.one("SELECT status FROM jobs WHERE id=?", (first_job,))["status"], "cancelled")
            self.assertIsNone(database.one("SELECT active_job_id FROM chat_threads WHERE id='a'")["active_job_id"])
            self.assertEqual(database.one("SELECT active_job_id FROM chat_threads WHERE id='b'")["active_job_id"], second_job)
            self.assertEqual(database.one("SELECT status FROM chat_messages WHERE id='ma'")["status"], "cancelled")


class SpreadsheetParsingTests(unittest.TestCase):
    def test_parses_required_fields_and_zip_url(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "TDoc_List"
            sheet.append([
                "TDoc", "Title", "Source", "Agenda item sort order", "Agenda item",
                "Agenda item description", "TDoc Status", "Abstract",
            ])
            sheet.append(["S2-2600001", "Proposal title", "Example Corp", 12, "20.3.1", "AI/ML", "available", "Summary"])
            workbook.save(path)
            proposals = parse_index(
                path,
                ["https://www.3gpp.org/ftp/example/Docs/S2-2600001.zip"],
                "https://www.3gpp.org/ftp/example/Docs/",
            )
            self.assertEqual(proposals[0]["tdoc"], "S2-2600001")
            self.assertEqual(proposals[0]["agenda_item"], "20.3.1")
            self.assertEqual(proposals[0]["primary_source"], "Example Corp")
            self.assertEqual(proposals[0]["canonical_source"], "Example Corp")
            self.assertTrue(proposals[0]["zip_url"].endswith("S2-2600001.zip"))

    def test_primary_source_uses_first_company(self):
        self.assertEqual(primary_source("Huawei, HiSilicon; China Mobile"), "Huawei")
        self.assertEqual(primary_source("  huawei；CMCC"), "huawei")
        self.assertEqual(primary_source("A/B & C"), "A/B & C")

    def test_canonicalizes_real_company_variants(self):
        for value, expected in (
            ("Apple.", "Apple"), ("[Apple", "Apple"), ("Ericsson Canada Inc.", "Ericsson"),
            ("[Penholders] InterDigital Inc.", "InterDigital"),
            ("NOKIA (acting as Pen Holder of Baseline)", "Nokia"),
            ("Rapporteur (Qualcomm Incorporated)", "Qualcomm"),
        ):
            self.assertEqual(canonical_source_name(value), expected)

    def test_uses_short_parent_present_in_same_meeting(self):
        rows = [
            {"primary_source": "Example Networks", "canonical_source": "Example Networks"},
            {"primary_source": "Example Networks Canada Ltd.", "canonical_source": "Example Networks Canada"},
            {"primary_source": "China Telecom", "canonical_source": "China Telecom"},
        ]
        apply_meeting_parent_names(rows)
        self.assertEqual(rows[1]["canonical_source"], "Example Networks")
        self.assertEqual(rows[2]["canonical_source"], "China Telecom")


class ArchiveSafetyTests(unittest.TestCase):
    def test_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                safe_extract(archive, Path(temp) / "out")

    def test_extracts_normal_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "safe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("S2-2600001/document.txt", "proposal")
            files = safe_extract(archive, Path(temp) / "out")
            self.assertEqual(files[0].read_text(), "proposal")


class SourceFacetTests(unittest.TestCase):
    def test_groups_and_filters_by_primary_source_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "sources.sqlite3")
            now = utc_now()
            database.execute(
                "INSERT INTO meetings(id,name,source_url,proposal_count,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("meeting", "SA2", "https://www.3gpp.org/ftp/test/Docs/", 3, now, now),
            )
            rows = [
                ("p1", "S2-1", "Huawei", "Huawei"),
                ("p2", "S2-2", "Huawei, HiSilicon", "Huawei"),
                ("p3", "S2-3", "huawei; China Mobile", "huawei"),
            ]
            for proposal_id, tdoc, source, primary in rows:
                database.execute(
                    """INSERT INTO proposals(id,meeting_id,tdoc,title,source,primary_source,canonical_source,agenda_item,agenda_description,zip_url)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (proposal_id, "meeting", tdoc, "Title", source, primary, "Huawei", "20.1", "Agenda", f"https://www.3gpp.org/ftp/test/Docs/{tdoc}.zip"),
                )
            result = list_proposals("meeting", source="HUAWEI", database=database)
            self.assertEqual(result["total"], 3)
            self.assertEqual(result["facets"]["sources"], ["all", "Huawei"])

    def test_combines_agenda_and_source_multiselect(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "multi.sqlite3")
            now = utc_now()
            database.execute("INSERT INTO meetings(id,name,source_url,proposal_count,created_at,updated_at) VALUES(?,?,?,?,?,?)", ("m", "M", "https://www.3gpp.org/ftp/test/Docs/", 3, now, now))
            for index, (agenda, source) in enumerate((("20.1", "Apple"), ("20.2", "Ericsson"), ("20.3", "Apple"))):
                database.execute("""INSERT INTO proposals(id,meeting_id,tdoc,title,source,primary_source,canonical_source,agenda_item,agenda_description,zip_url) VALUES(?,?,?,?,?,?,?,?,?,?)""", (f"p{index}", "m", f"S2-{index}", "Title", source, source, source, agenda, "Agenda", f"https://www.3gpp.org/ftp/test/Docs/S2-{index}.zip"))
            result = list_proposals("m", agenda=["20.1", "20.2"], source=["Apple", "Ericsson"], database=database)
            self.assertEqual(result["total"], 2)
            agenda_first = list_proposals("m", agenda=["20.1"], database=database)
            self.assertEqual(agenda_first["facets"]["sources"], ["all", "Apple"])
            source_first = list_proposals("m", source=["Apple"], database=database)
            self.assertEqual(source_first["facets"]["agendas"], ["all", "20.1", "20.3"])

    def test_user_alias_rebuilds_existing_proposals(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "alias.sqlite3")
            now = utc_now()
            database.execute("INSERT INTO meetings(id,name,source_url,proposal_count,created_at,updated_at) VALUES(?,?,?,?,?,?)", ("m", "M", "https://www.3gpp.org/ftp/test/Docs/", 1, now, now))
            database.execute("""INSERT INTO proposals(id,meeting_id,tdoc,title,source,agenda_item,agenda_description,zip_url) VALUES(?,?,?,?,?,?,?,?)""", ("p", "m", "S2-1", "Title", "Example Labs Ltd.", "20.1", "Agenda", "https://www.3gpp.org/ftp/test/Docs/S2-1.zip"))
            save_source_alias(database, "Example Labs Ltd.", "Example Group", now)
            self.assertEqual(database.one("SELECT canonical_source FROM proposals WHERE id='p'")["canonical_source"], "Example Group")


class VisualEvidenceTests(unittest.TestCase):
    def test_extracts_native_office_chart_without_vision_model(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "chart.pptx"
            with zipfile.ZipFile(path, "w") as bundle:
                bundle.writestr("ppt/charts/chart1.xml", "<chart><title><v>Traffic</v></title><ser><v>2026</v><v>42</v></ser></chart>")
            evidence = extract_office_visual_evidence(path)
            self.assertEqual(evidence[0]["kind"], "office_chart")
            self.assertTrue(evidence[0]["resolved"])
            self.assertIn("series", evidence[0])
            self.assertIn("values", evidence[0])


class PackageJobTests(unittest.TestCase):
    def test_download_package_keeps_original_zips(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "test.sqlite3")
            now = utc_now()
            database.execute(
                "INSERT INTO meetings(id,name,source_url,proposal_count,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                ("meeting", "SA2-test", "https://www.3gpp.org/ftp/test/Docs/", 1, now, now),
            )
            database.execute(
                """INSERT INTO proposals(id,meeting_id,tdoc,title,source,agenda_item,agenda_description,zip_url)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("proposal", "meeting", "S2-2600001", "Title", "Source", "20.1", "Agenda", "https://www.3gpp.org/ftp/test/Docs/S2-2600001.zip"),
            )
            original = Path(temp) / "S2-2600001.zip"
            with zipfile.ZipFile(original, "w") as bundle:
                bundle.writestr("document.txt", "content")
            runner = JobRunner(database)
            runner._ensure_zip = lambda proposal: (original, {"bytes": original.stat().st_size, "cached": True})  # type: ignore[method-assign]
            job_id = database.create_job("download", {"meeting_id": "meeting", "proposal_ids": ["proposal"]})
            result = runner._download_job(job_id, {"meeting_id": "meeting", "proposal_ids": ["proposal"]})
            package = Path(result["file_path"])
            with zipfile.ZipFile(package) as bundle:
                self.assertIn("proposals/S2-2600001.zip", bundle.namelist())
                self.assertIn("manifest.csv", bundle.namelist())


class AnalysisAndReportTests(unittest.TestCase):
    def _database(self, temp: str) -> Database:
        database = Database(Path(temp) / "analysis.sqlite3")
        now = utc_now()
        database.execute(
            "INSERT INTO meetings(id,name,source_url,proposal_count,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("meeting-report", "SA2-report", "https://www.3gpp.org/ftp/test/Docs/", 1, now, now),
        )
        database.execute(
            """INSERT INTO proposals(id,meeting_id,tdoc,title,source,agenda_item,agenda_description,zip_url)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("proposal-report", "meeting-report", "S2-2600999", "Architecture proposal", "Example", "20.6.0", "6G Architecture", "https://www.3gpp.org/ftp/test/Docs/S2-2600999.zip"),
        )
        return database

    def test_analysis_extracts_text_and_creates_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            database = self._database(temp)
            archive = Path(temp) / "proposal.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("proposal.txt", "This proposal changes the architecture and identifies a dependency.")
            runner = JobRunner(database)
            runner._ensure_zip = lambda proposal: (archive, {"bytes": archive.stat().st_size})  # type: ignore[method-assign]
            job_id = database.create_job("analysis", {"meeting_id": "meeting-report", "proposal_ids": ["proposal-report"]})
            result = runner._analysis_job(job_id, {"meeting_id": "meeting-report", "proposal_ids": ["proposal-report"]})
            self.assertEqual(result["completed_count"], 1)
            preparation = database.one("SELECT * FROM preparations WHERE proposal_id='proposal-report'")
            self.assertIn("changes the architecture", preparation["content"])
            analysis = database.one("SELECT * FROM analyses WHERE proposal_id='proposal-report'")
            self.assertIn("changes the architecture", analysis["content"])
            chat_id = database.create_job("chat", {"meeting_id": "meeting-report", "proposal_ids": ["proposal-report"]})
            chat = runner._chat_job(chat_id, {
                "meeting_id": "meeting-report", "proposal_ids": ["proposal-report"],
                "question": "What changes?", "thread_id": None,
            })
            self.assertEqual(chat["successful_proposals"], ["S2-2600999"])
            self.assertIn("thread_id", chat)
            self.assertTrue(database.one("SELECT id FROM job_events WHERE job_id=? AND event_type='answer_delta'", (chat_id,)))
            self.assertEqual(database.one("SELECT status FROM chat_messages WHERE thread_id=? AND role='assistant'", (chat["thread_id"],))["status"], "completed")

    def test_report_intent_detection(self):
        self.assertTrue(JobRunner._is_report_request("请生成一份 PPT 报告"))
        self.assertTrue(JobRunner._is_report_request("/word summarize the proposals"))
        self.assertFalse(JobRunner._is_report_request("比较这些提案的核心差异"))

    def test_generates_docx_and_pptx_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            database = self._database(temp)
            request = {
                "meeting_id": "meeting-report",
                "proposal_ids": ["proposal-report"],
                "format": "docx",
                "title": "Proposal Analysis",
                "language": "en",
                "template_id": None,
                "source_thread_id": "thread-history",
                "source_message_ids": ["message-1"],
                "conversation_context": {"recent_messages": [{"role": "assistant", "content": "Prior risk analysis"}]},
            }
            doc_report = create_outline(request, database)
            stored = database.one("SELECT * FROM reports WHERE id=?", (doc_report["id"],))
            self.assertEqual(stored["source_thread_id"], "thread-history")
            self.assertIn("Prior risk analysis", stored["context_snapshot_json"])
            doc_result = render_report(doc_report["id"], doc_report["outline"], database)
            self.assertTrue(Path(doc_result["file_path"]).exists())
            self.assertTrue(zipfile.is_zipfile(doc_result["file_path"]))

            request["format"] = "pptx"
            ppt_report = create_outline(request, database)
            ppt_result = render_report(ppt_report["id"], ppt_report["outline"], database)
            self.assertTrue(Path(ppt_result["file_path"]).exists())
            self.assertTrue(zipfile.is_zipfile(ppt_result["file_path"]))


if __name__ == "__main__":
    unittest.main()
