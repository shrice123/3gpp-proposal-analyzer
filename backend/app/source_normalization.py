from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any


LEGAL_SUFFIXES = re.compile(
    r"\b(?:incorporated|incorporate|inc|limited|ltd|llc|corporation|corp|company|co|gmbh|"
    r"plc|n\.?v|b\.?v|s\.?a|s\.?a\.?s|ab|oy)\.?$",
    re.IGNORECASE,
)
ROLE_PREFIX = re.compile(r"^(?:\[?penholders?\]?\s*|rapporteurs?\s*)", re.IGNORECASE)
ROLE_NOTE = re.compile(
    r"\s*\((?:acting as|baseline|solution|s\d?-\d+|pen[- ]?holder|rapporteur|rev\b).*?$",
    re.IGNORECASE,
)


BUILTIN_ALIASES: dict[str, str] = {
    "apple": "Apple",
    "ericsson": "Ericsson",
    "ericsson canada": "Ericsson",
    "telefonaktiebolaget lm ericsson": "Ericsson",
    "huawei": "Huawei",
    "nokia": "Nokia",
    "interdigital": "InterDigital",
    "interdigital belgium": "InterDigital",
    "interdigital canada": "InterDigital",
    "interdigital communications": "InterDigital",
    "interdigital washington dc": "InterDigital",
    "qualcomm": "Qualcomm",
    "qualcomm europe italy": "Qualcomm",
    "qualcomm innovation center": "Qualcomm",
    "qualcomm korea": "Qualcomm",
    "qualcomm technologies": "Qualcomm",
    "jio platform": "Jio Platforms",
    "jio platforms": "Jio Platforms",
    "china mobile": "China Mobile",
    "china mobile hangzhou inf": "China Mobile",
    "china mobile com": "China Mobile",
    "china telecom": "China Telecom",
    "china telecommunications": "China Telecom",
    "lenovo": "Lenovo",
    "lenovo beijing": "Lenovo",
    "mediatek": "MediaTek",
    "mediatek hefei": "MediaTek",
    "nec": "NEC",
    "oracle": "Oracle",
    "sony": "Sony",
    "futurewei": "Futurewei",
    "futurewei technologies": "Futurewei",
    "hipe ip": "HIPE IP",
    "kyocera": "Kyocera",
    "lg electronics": "LG Electronics",
    "oppo": "OPPO",
    "samsung": "Samsung",
    "vivo": "vivo",
    "zte": "ZTE",
    "xiaomi": "Xiaomi",
    "tejas network": "Tejas Networks",
    "tejas networks": "Tejas Networks",
}


def _plain(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").replace("\u00a0", " ").strip()


def extract_primary_source(source: str) -> str:
    value = _plain(source)
    if not value:
        return "Unknown"
    rapporteur = re.match(r"^rapporteurs?\s*\(([^,;\]\)]+)", value, re.IGNORECASE)
    if rapporteur:
        value = rapporteur.group(1)
    value = ROLE_PREFIX.sub("", value)
    value = re.sub(r"^\[[^\]]*\]\s*", "", value)
    value = value.lstrip("[({ ")
    value = re.split(r"[,，;；\n\r\[]+", value, maxsplit=1)[0]
    value = ROLE_NOTE.sub("", value)
    value = re.sub(r"\s*\((?:pen[- ]?holder|solution penholder|acting as).*?$", "", value, flags=re.I)
    value = value.strip("[](){} .,:;!?")
    if not value or value.casefold() in {"company name(s)", "-", "unknown"}:
        return "Unknown"
    return value


def alias_key(value: str) -> str:
    text = _plain(value).casefold()
    text = re.sub(r"\([^)]*(?:pen.?holder|rapporteur|acting as|rev\b)[^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    previous = None
    while text and text != previous:
        previous = text
        text = LEGAL_SUFFIXES.sub("", text).strip()
    return text or "unknown"


def canonical_source_name(source: str, user_aliases: dict[str, str] | None = None) -> str:
    primary = extract_primary_source(source)
    key = alias_key(primary)
    if user_aliases and key in user_aliases:
        return user_aliases[key]
    if key in BUILTIN_ALIASES:
        return BUILTIN_ALIASES[key]
    return primary.rstrip(".").strip() or "Unknown"


def apply_meeting_parent_names(rows: list[dict[str, Any]], user_aliases: dict[str, str] | None = None) -> None:
    """Merge subsidiary-style names only when a credible shorter parent is present in the same meeting."""
    known_parents = {value.casefold() for value in BUILTIN_ALIASES.values()}
    candidates = {
        item["canonical_source"]: alias_key(item["canonical_source"])
        for item in rows if item.get("canonical_source") and item["canonical_source"] != "Unknown"
    }
    for item in rows:
        primary_key = alias_key(item.get("primary_source") or item.get("source") or "")
        if user_aliases and primary_key in user_aliases:
            continue
        matches = [
            (display, key) for display, key in candidates.items()
            if primary_key.startswith(key + " ")
            and (len(key.split()) >= 2 or display.casefold() in known_parents)
        ]
        if matches:
            item["canonical_source"] = max(matches, key=lambda candidate: len(candidate[1]))[0]


def rebuild_canonical_sources(database: Any) -> int:
    aliases = {
        row["alias_key"]: row["canonical_name"]
        for row in database.query("SELECT alias_key,canonical_name FROM source_aliases")
    }
    rows = database.query("SELECT id,meeting_id,source FROM proposals")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row["primary_source"] = extract_primary_source(row["source"])
        row["canonical_source"] = canonical_source_name(row["primary_source"], aliases)
        grouped.setdefault(row["meeting_id"], []).append(row)
    for meeting_rows in grouped.values():
        apply_meeting_parent_names(meeting_rows, aliases)
    with database.transaction() as connection:
        for row in rows:
            connection.execute(
                "UPDATE proposals SET primary_source=?,canonical_source=? WHERE id=?",
                (row["primary_source"], row["canonical_source"], row["id"]),
            )
    return len(rows)


def save_source_alias(database: Any, alias: str, canonical_name: str, now: str) -> dict[str, Any]:
    cleaned_alias = extract_primary_source(alias)
    cleaned_canonical = extract_primary_source(canonical_name)
    key = alias_key(cleaned_alias)
    existing = database.one("SELECT id FROM source_aliases WHERE alias_key=?", (key,))
    alias_id = existing["id"] if existing else uuid.uuid4().hex
    database.execute(
        """INSERT INTO source_aliases(id,alias,alias_key,canonical_name,updated_at) VALUES(?,?,?,?,?)
           ON CONFLICT(alias_key) DO UPDATE SET alias=excluded.alias,canonical_name=excluded.canonical_name,
           updated_at=excluded.updated_at""",
        (alias_id, cleaned_alias, key, cleaned_canonical, now),
    )
    rebuild_canonical_sources(database)
    return {"id": alias_id, "alias": cleaned_alias, "canonical_name": cleaned_canonical, "built_in": False}
