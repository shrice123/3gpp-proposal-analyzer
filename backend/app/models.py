from __future__ import annotations

import base64
import json
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from .database import Database, _rebuild_model_registry, db, utc_now
from .security import decrypt_secret, encrypt_secret


PROVIDER_DEFAULTS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "custom": "",
}


def save_profile(payload: dict[str, Any], database: Database = db) -> dict[str, Any]:
    profile_id = payload.get("id") or uuid.uuid4().hex
    encrypted = encrypt_secret(payload["api_key"]) if payload.get("api_key") else None
    if payload.get("is_default", True):
        database.execute("UPDATE model_profiles SET is_default=0")
    database.execute(
        """INSERT INTO model_profiles(id,name,provider,base_url,text_model,vision_model,embedding_model,
           api_key_encrypted,available_models_json,deployment,max_output_tokens,context_window,max_concurrency,
           request_timeout_seconds,enabled,is_default,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name,provider=excluded.provider,
           base_url=excluded.base_url,text_model=excluded.text_model,vision_model=excluded.vision_model,
           embedding_model=excluded.embedding_model,
           api_key_encrypted=COALESCE(excluded.api_key_encrypted,model_profiles.api_key_encrypted),
           available_models_json=excluded.available_models_json,deployment=excluded.deployment,
           max_output_tokens=excluded.max_output_tokens,context_window=excluded.context_window,
           max_concurrency=excluded.max_concurrency,request_timeout_seconds=excluded.request_timeout_seconds,
           enabled=excluded.enabled,
           is_default=excluded.is_default,updated_at=excluded.updated_at""",
        (
            profile_id,
            payload["name"],
            payload["provider"],
            payload.get("base_url") or PROVIDER_DEFAULTS[payload["provider"]],
            payload.get("text_model", ""),
            payload.get("vision_model", ""),
            payload.get("embedding_model", ""),
            encrypted,
            json.dumps(list(dict.fromkeys([payload.get("text_model", ""), *(payload.get("available_models") or [])]))),
            payload.get("deployment", "external"), payload.get("max_output_tokens", 8192),
            payload.get("context_window", 65536), payload.get("max_concurrency", 4),
            payload.get("request_timeout_seconds", 180), 1 if payload.get("enabled", True) else 0,
            1 if payload.get("is_default", True) else 0,
            utc_now(),
        ),
    )
    with database.transaction() as connection:
        _rebuild_model_registry(connection)
    return public_profile(database.one("SELECT * FROM model_profiles WHERE id=?", (profile_id,)))


def public_profile(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": row["id"],
        "name": row["name"],
        "provider": row["provider"],
        "base_url": row["base_url"],
        "text_model": row["text_model"],
        "vision_model": row["vision_model"],
        "embedding_model": row["embedding_model"],
        "available_models": json.loads(row.get("available_models_json") or "[]"),
        "deployment": row.get("deployment", "external"),
        "max_output_tokens": row.get("max_output_tokens", 8192),
        "context_window": row.get("context_window", 65536),
        "max_concurrency": row.get("max_concurrency", 4),
        "request_timeout_seconds": row.get("request_timeout_seconds", 180),
        "enabled": bool(row.get("enabled", 1)),
        "has_api_key": bool(row.get("api_key_encrypted")),
        "is_default": bool(row["is_default"]),
    }


def default_profile(database: Database = db) -> dict[str, Any] | None:
    return database.one("SELECT * FROM model_profiles ORDER BY is_default DESC, updated_at DESC LIMIT 1")


def model_options(database: Database = db) -> list[dict[str, Any]]:
    rows = database.query(
        """SELECT o.*,p.provider,p.deployment,p.is_default,p.text_model FROM model_options o
           JOIN model_profiles p ON p.id=o.profile_id
           WHERE p.enabled=1 AND p.api_key_encrypted IS NOT NULL ORDER BY p.is_default DESC,o.display_name COLLATE NOCASE"""
    )
    return [{
        "id": row["id"], "profile_id": row["profile_id"], "model": row["model_id"],
        "label": f"{row['provider'].title()} / {row['display_name']}", "provider": row["provider"],
        "deployment": row["deployment"], "revision": row["revision"],
        "is_default": bool(row["is_default"] and row["model_id"] == row["text_model"]),
    } for row in rows]


def resolve_model_option(option_id: str | None, database: Database = db) -> tuple[dict[str, Any] | None, str]:
    if option_id:
        option = database.one(
            """SELECT o.model_id,o.revision,p.* FROM model_options o JOIN model_profiles p ON p.id=o.profile_id
               WHERE o.id=? AND p.enabled=1""", (option_id,),
        )
        if option and option.get("api_key_encrypted"):
            option["_option_revision"] = option["revision"]
            return option, option["model_id"]
    if option_id and ":" in option_id:
        profile_id, model = option_id.split(":", 1)
        profile = database.one("SELECT * FROM model_profiles WHERE id=? AND enabled=1", (profile_id,))
        if profile and profile.get("api_key_encrypted"):
            return profile, model
    profile = default_profile(database)
    return profile, str(profile.get("text_model") or "") if profile else ""


def discover_models(profile_id: str, database: Database = db) -> list[str]:
    profile = database.one("SELECT * FROM model_profiles WHERE id=?", (profile_id,))
    if not profile or not profile.get("api_key_encrypted"):
        raise ValueError("模型配置不存在或未保存 API Key")
    response = httpx.get(
        f"{profile['base_url'].rstrip('/')}/models",
        headers={"Authorization": f"Bearer {decrypt_secret(profile['api_key_encrypted'])}"},
        timeout=min(int(profile.get("request_timeout_seconds") or 180), 30),
    )
    response.raise_for_status()
    models = sorted({str(item.get("id")) for item in response.json().get("data", []) if item.get("id")})
    database.execute("UPDATE model_profiles SET available_models_json=?,updated_at=? WHERE id=?", (json.dumps(models), utc_now(), profile_id))
    with database.transaction() as connection:
        _rebuild_model_registry(connection)
    return models


def stream_text_model(
    messages: list[dict[str, str]], database: Database = db, *, model_option_id: str | None = None,
    max_tokens: int | None = None, on_delta: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None, continuations: int = 0,
    timeout_seconds: int | None = None,
    max_attempts: int = 3,
) -> tuple[str, str, dict[str, Any]]:
    profile, model = resolve_model_option(model_option_id, database)
    if not profile or not profile.get("api_key_encrypted") or not model:
        return "", "unavailable", {}
    limit = int(max_tokens or profile.get("max_output_tokens") or 8192)
    total_timeout = float(timeout_seconds or profile.get("request_timeout_seconds") or 180)
    timeout = httpx.Timeout(total_timeout, connect=min(30, total_timeout), read=min(60, total_timeout), write=min(60, total_timeout))
    output = ""
    finish_reason = "stop"
    usage: dict[str, Any] = {}
    working_messages = list(messages)
    for segment in range(continuations + 1):
        if cancelled and cancelled():
            return output, "cancelled", usage
        last_error: Exception | None = None
        stream_succeeded = False
        for attempt in range(max_attempts):
            try:
                request_payload: dict[str, Any] = {"model": model, "messages": working_messages, "temperature": 0.2, "max_tokens": limit, "stream": True}
                if attempt == 0:
                    request_payload["stream_options"] = {"include_usage": True}
                with httpx.stream(
                    "POST", f"{profile['base_url'].rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {decrypt_secret(profile['api_key_encrypted'])}", "Content-Type": "application/json"},
                    json=request_payload,
                    timeout=timeout,
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        response.raise_for_status()
                    response.raise_for_status()
                    segment_text = ""
                    for line in response.iter_lines():
                        if cancelled and cancelled():
                            return output + segment_text, "cancelled", usage
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        event = json.loads(data)
                        choice = (event.get("choices") or [{}])[0]
                        delta = (choice.get("delta") or {}).get("content") or ""
                        if delta:
                            segment_text += delta
                            if on_delta:
                                on_delta(delta)
                        finish_reason = choice.get("finish_reason") or finish_reason
                        usage = event.get("usage") or usage
                    output += segment_text
                    stream_succeeded = True
                    break
            except Exception as exc:
                last_error = exc
                if attempt == max_attempts - 1:
                    break
                retry_after = None
                if isinstance(exc, httpx.HTTPStatusError):
                    retry_after = exc.response.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 30) if retry_after else 1.5 * (attempt + 1)
                except ValueError:
                    delay = 1.5 * (attempt + 1)
                time.sleep(delay)
        if not stream_succeeded or (not output and last_error):
            try:
                response = httpx.post(
                    f"{profile['base_url'].rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {decrypt_secret(profile['api_key_encrypted'])}", "Content-Type": "application/json"},
                    json={"model": model, "messages": working_messages, "temperature": 0.2, "max_tokens": limit, "stream": False},
                    timeout=timeout,
                )
                response.raise_for_status()
                data = response.json()
                choice = (data.get("choices") or [{}])[0]
                segment_text = (choice.get("message") or {}).get("content") or ""
                if segment_text:
                    output += segment_text
                    if on_delta:
                        on_delta(segment_text)
                finish_reason = choice.get("finish_reason") or "stop"
                usage = data.get("usage") or usage
            except Exception as fallback_error:
                raise RuntimeError(f"模型服务不可用：{fallback_error}") from (last_error or fallback_error)
        if finish_reason != "length" or segment >= continuations:
            break
        working_messages = [*messages, {"role": "assistant", "content": output}, {"role": "user", "content": "请从中断处继续，不要重复已输出内容。"}]
    return output, finish_reason, usage


def call_text_model(messages: list[dict[str, str]], database: Database = db, max_tokens: int = 8192, model_option_id: str | None = None) -> str:
    return stream_text_model(messages, database, model_option_id=model_option_id, max_tokens=max_tokens)[0]


def call_vision_model(image_path: Path, context: str, database: Database = db, model_option_id: str | None = None) -> str:
    profile, _ = resolve_model_option(model_option_id, database)
    if not profile or not profile["api_key_encrypted"] or not profile["vision_model"]:
        return ""
    mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return ""
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    api_key = decrypt_secret(profile["api_key_encrypted"])
    response = httpx.post(
        f"{profile['base_url'].rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": profile["vision_model"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze this visual from a 3GPP proposal. Describe the diagram/chart, "
                                "important entities and relationships, technical conclusion, and uncertainties. "
                                "Do not invent unreadable labels.\n" + context
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 900,
        },
        timeout=float(profile.get("request_timeout_seconds") or 180),
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def test_profile(payload: dict[str, Any]) -> dict[str, Any]:
    profile = payload.copy()
    api_key = profile.get("api_key", "")
    if not api_key and profile.get("id"):
        stored = db.one("SELECT * FROM model_profiles WHERE id=?", (profile["id"],))
        api_key = decrypt_secret(stored["api_key_encrypted"]) if stored else ""
    if not api_key:
        return {"ok": False, "message": "请输入 API Key"}
    base_url = (profile.get("base_url") or PROVIDER_DEFAULTS.get(profile.get("provider", "custom"), "")).rstrip("/")
    try:
        response = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20,
        )
        if response.status_code < 400:
            if profile.get("id"):
                db.execute("UPDATE model_profiles SET last_test_ok=1,last_tested_at=?,updated_at=? WHERE id=?", (utc_now(), utc_now(), profile["id"]))
                with db.transaction() as connection:
                    _rebuild_model_registry(connection)
            return {"ok": True, "message": "连接成功", "status_code": response.status_code}
        if profile.get("id"):
            db.execute("UPDATE model_profiles SET last_test_ok=0,last_tested_at=? WHERE id=?", (utc_now(), profile["id"]))
        return {"ok": False, "message": f"服务返回 HTTP {response.status_code}"}
    except Exception as exc:
        if profile.get("id"):
            db.execute("UPDATE model_profiles SET last_test_ok=0,last_tested_at=? WHERE id=?", (utc_now(), profile["id"]))
        return {"ok": False, "message": str(exc)}


def local_summary(text: str, title: str) -> dict[str, Any]:
    compact = " ".join(text.split())
    excerpt = compact[:1800]
    return {
        "purpose": f"Analysis of {title}",
        "core_change": excerpt[:420] or "No extractable text was found.",
        "technical_approach": excerpt[420:840],
        "impact": excerpt[840:1200],
        "risks": "Review proposal assumptions, dependencies, and interactions with related TDocs.",
        "recommendation": "Validate against the referenced specification clauses and meeting discussion.",
    }


def summarize_with_model(
    text: str, title: str, tdoc: str, database: Database = db, model_option_id: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    prompt = (
        "Return strict JSON with keys purpose, core_change, technical_approach, impact, risks, recommendation. "
        "Write in English and preserve 3GPP terminology. Do not invent facts.\n\n"
        f"TDoc: {tdoc}\nTitle: {title}\nContent:\n{text[:30000]}"
    )
    try:
        output = stream_text_model(
            [
                {"role": "system", "content": "You are a precise 3GPP proposal analyst."},
                {"role": "user", "content": prompt},
            ],
            database, max_tokens=2600, model_option_id=model_option_id, cancelled=cancelled,
        )[0]
        if output:
            cleaned = output.strip().removeprefix("```json").removesuffix("```").strip()
            return json.loads(cleaned)
    except Exception:
        pass
    return local_summary(text, title)
