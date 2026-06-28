#!/usr/bin/env python3
"""Local state helpers for batch imports and manual update tracking."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from knowledge_sync import PROJECT_ROOT, normalize_source_url


STATE_VERSION = 1
VALID_FREQUENCIES = {"daily", "weekly", "monthly"}
LOOKBACK_DAYS = {"daily": 2, "weekly": 8, "monthly": 35}


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "subscriptions": [],
        "articles": {},
    }


def state_path(config: dict[str, Any]) -> Path:
    configured = str((config.get("tracking") or {}).get("state_path") or "wechat_clipper_state.json")
    path = Path(configured).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_state(config: dict[str, Any]) -> dict[str, Any]:
    path = state_path(config)
    if not path.exists():
        return empty_state()
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("version", STATE_VERSION)
    state.setdefault("subscriptions", [])
    state.setdefault("articles", {})
    return state


def save_state(config: dict[str, Any], state: dict[str, Any]) -> Path:
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> dt.date:
    return dt.date.today()


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def tracking_window(frequency: str, last_success_at: str | None, *, today_date: dt.date | None = None) -> tuple[dt.date, dt.date]:
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(f"未知追踪频率：{frequency}")
    end = today_date or today()
    default_start = end - dt.timedelta(days=LOOKBACK_DAYS[frequency] - 1)
    if not last_success_at:
        return default_start, end

    last_success_date = parse_iso_date_prefix(last_success_at)
    if last_success_date and last_success_date < default_start:
        return last_success_date - dt.timedelta(days=1), end
    return default_start, end


def parse_iso_date_prefix(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def subscription_id(account: str, seed_url: str) -> str:
    raw = f"{account}|{normalize_source_url(seed_url)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def upsert_subscription(
    state: dict[str, Any],
    *,
    name: str,
    frequency: str,
    seed_url: str,
    account: str,
    biz: str | None,
    user_name: str | None,
    fakeid: str | None,
) -> dict[str, Any]:
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(f"未知追踪频率：{frequency}")
    sub_id = subscription_id(account or name, seed_url)
    subscriptions = state.setdefault("subscriptions", [])
    existing = next((item for item in subscriptions if item.get("id") == sub_id), None)
    payload = {
        "id": sub_id,
        "name": name,
        "frequency": frequency,
        "seed_url": normalize_source_url(seed_url),
        "account": account,
        "biz": biz or "",
        "user_name": user_name or "",
        "fakeid": fakeid or "",
        "updated_at": now_iso(),
    }
    if existing:
        existing.update(payload)
        return existing

    payload["created_at"] = payload["updated_at"]
    payload["last_success_at"] = ""
    payload["last_run_at"] = ""
    subscriptions.append(payload)
    return payload


def subscriptions_for(
    state: dict[str, Any],
    *,
    frequency: str | None = None,
    name: str | None = None,
) -> list[dict[str, Any]]:
    result = list(state.get("subscriptions") or [])
    if frequency:
        result = [item for item in result if item.get("frequency") == frequency]
    if name:
        result = [item for item in result if item.get("name") == name or item.get("id") == name]
    return result


def article_record(state: dict[str, Any], url: str) -> dict[str, Any] | None:
    return (state.get("articles") or {}).get(normalize_source_url(url))


def classify_article(state: dict[str, Any], url: str, content_sha256: str | None) -> dict[str, Any]:
    normalized_url = normalize_source_url(url)
    record = article_record(state, normalized_url)
    if not record:
        return {
            "action": "create",
            "version": 1,
            "force_create": False,
            "title_suffix": None,
            "reason": "new_url",
        }

    latest_sha = record.get("content_sha256")
    if content_sha256 and latest_sha == content_sha256:
        return {
            "action": "skip",
            "version": int(record.get("latest_version") or 1),
            "force_create": False,
            "title_suffix": None,
            "reason": "unchanged_sha256",
        }

    version = int(record.get("latest_version") or len(record.get("versions") or []) or 1) + 1
    return {
        "action": "create_version",
        "version": version,
        "force_create": True,
        "title_suffix": f"v{version}",
        "reason": "changed_sha256",
    }


def record_article_version(
    state: dict[str, Any],
    *,
    url: str,
    title: str,
    account: str | None,
    author: str | None,
    publish_time: str | None,
    content_sha256: str | None,
    version: int,
    source_markdown: str,
    notion_page_url: str | None,
    obsidian_markdown: str | None,
) -> dict[str, Any]:
    normalized_url = normalize_source_url(url)
    articles = state.setdefault("articles", {})
    record = articles.setdefault(
        normalized_url,
        {
            "url": normalized_url,
            "title": title,
            "account": account or "",
            "author": author or "",
            "publish_time": publish_time or "",
            "versions": [],
        },
    )
    record.update(
        {
            "title": title,
            "account": account or "",
            "author": author or "",
            "publish_time": publish_time or "",
            "content_sha256": content_sha256 or "",
            "latest_version": version,
            "last_synced_at": now_iso(),
        }
    )
    version_entry = {
        "version": version,
        "content_sha256": content_sha256 or "",
        "source_markdown": source_markdown,
        "notion_page_url": notion_page_url or "",
        "obsidian_markdown": obsidian_markdown or "",
        "synced_at": record["last_synced_at"],
    }
    versions = [item for item in record.get("versions") or [] if int(item.get("version") or 0) != version]
    versions.append(version_entry)
    record["versions"] = sorted(versions, key=lambda item: int(item.get("version") or 0))
    return record


def mark_subscription_run(
    subscription: dict[str, Any],
    *,
    success: bool,
    started_at: str,
    error: str | None = None,
) -> None:
    subscription["last_run_at"] = started_at
    subscription["last_error"] = error or ""
    if success:
        subscription["last_success_at"] = now_iso()
