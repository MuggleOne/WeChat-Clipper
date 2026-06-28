#!/usr/bin/env python3
"""Discover WeChat Official Account articles through a local mp.weixin login."""

from __future__ import annotations

import datetime as dt
import html
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


WECHAT_MP_BASE = "https://mp.weixin.qq.com/cgi-bin"
CN_TZ = dt.timezone(dt.timedelta(hours=8))


class WeChatMpDiscoveryError(RuntimeError):
    """Raised when the logged-in WeChat MP discovery API cannot be used."""


class WeChatMpAuthError(WeChatMpDiscoveryError):
    """Raised when cookie/token configuration is missing or rejected."""


@dataclass(frozen=True)
class DiscoveredArticle:
    title: str
    url: str
    publish_ts: int | None
    publish_time: str | None
    item_show_type: int | None = None
    digest: str | None = None
    cover: str | None = None
    appmsgid: str | None = None
    aid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "publish_ts": self.publish_ts,
            "publish_time": self.publish_time,
            "item_show_type": self.item_show_type,
            "digest": self.digest,
            "cover": self.cover,
            "appmsgid": self.appmsgid,
            "aid": self.aid,
        }


FetchJson = Callable[[str, dict[str, str], int], dict[str, Any]]
SleepFunc = Callable[[float], None]


def discover_account_articles(
    config: dict[str, Any],
    *,
    account_name: str,
    fakeid: str | None = None,
    from_date: dt.date,
    to_date: dt.date,
    timeout: int = 30,
    fetch_json_func: FetchJson | None = None,
    sleep_func: SleepFunc | None = None,
) -> tuple[str, list[DiscoveredArticle]]:
    """Return the account fakeid and articles in the inclusive date range."""

    mp_config = normalized_mp_config(config)
    fetcher = fetch_json_func or fetch_json
    sleeper = sleep_func or time.sleep
    resolved_fakeid = fakeid or resolve_fakeid(
        mp_config,
        account_name=account_name,
        timeout=timeout,
        fetch_json_func=fetcher,
    )

    articles: list[DiscoveredArticle] = []
    page_size = int(mp_config.get("page_size") or 5)
    max_pages = int(mp_config.get("max_pages") or 20)
    delay = float(mp_config.get("request_delay_seconds") or 0)

    for page in range(max_pages):
        begin = page * page_size
        payload = fetcher(
            build_appmsg_url(mp_config, resolved_fakeid, begin, page_size),
            request_headers(mp_config),
            timeout,
        )
        ensure_success(payload, "list_ex")
        items = payload.get("app_msg_list") or []
        if not items:
            break

        oldest_date: dt.date | None = None
        for item in items:
            article = parse_appmsg_item(item)
            if not article.url:
                continue
            published_date = date_from_timestamp(article.publish_ts)
            if published_date:
                oldest_date = min(oldest_date, published_date) if oldest_date else published_date
                if from_date <= published_date <= to_date:
                    articles.append(article)

        if oldest_date and oldest_date < from_date:
            break
        if delay and page < max_pages - 1:
            sleeper(delay)

    deduped = dedupe_articles(articles)
    return resolved_fakeid, sorted(deduped, key=article_sort_key)


def resolve_fakeid(
    mp_config: dict[str, Any],
    *,
    account_name: str,
    timeout: int = 30,
    fetch_json_func: FetchJson | None = None,
) -> str:
    if not account_name:
        raise WeChatMpDiscoveryError("缺少公众号名称，无法搜索 fakeid。")

    fetcher = fetch_json_func or fetch_json
    payload = fetcher(
        build_searchbiz_url(mp_config, account_name),
        request_headers(mp_config),
        timeout,
    )
    ensure_success(payload, "search_biz")
    accounts = payload.get("list") or []
    if not accounts:
        raise WeChatMpDiscoveryError(f"没有搜索到公众号：{account_name}")

    normalized_query = normalize_text(account_name)
    exact_matches = [
        account
        for account in accounts
        if normalize_text(str(account.get("nickname") or "")) == normalized_query
    ]
    selected = exact_matches[0] if exact_matches else accounts[0]
    fakeid = str(selected.get("fakeid") or "").strip()
    if not fakeid:
        raise WeChatMpDiscoveryError(f"公众号搜索结果缺少 fakeid：{account_name}")
    return fakeid


def normalized_mp_config(config: dict[str, Any]) -> dict[str, Any]:
    mp_config = dict(config.get("wechat_mp") or {})
    cookie = str(mp_config.get("cookie") or "").strip()
    token = str(mp_config.get("token") or "").strip()
    if not cookie or cookie.startswith("your_"):
        raise WeChatMpAuthError("缺少 wechat_mp.cookie，请在本地配置或 WECHAT_MP_COOKIE 中填写。")
    if not token or token.startswith("your_"):
        raise WeChatMpAuthError("缺少 wechat_mp.token，请在本地配置或 WECHAT_MP_TOKEN 中填写。")
    mp_config["cookie"] = cookie
    mp_config["token"] = token
    mp_config["page_size"] = int(mp_config.get("page_size") or 5)
    mp_config["max_pages"] = int(mp_config.get("max_pages") or 20)
    mp_config["request_delay_seconds"] = float(mp_config.get("request_delay_seconds") or 0)
    return mp_config


def build_searchbiz_url(mp_config: dict[str, Any], query: str) -> str:
    params = {
        "action": "search_biz",
        "token": mp_config["token"],
        "lang": "zh_CN",
        "f": "json",
        "ajax": "1",
        "random": f"{time.time():.17f}",
        "query": query,
        "begin": "0",
        "count": "5",
    }
    return f"{WECHAT_MP_BASE}/searchbiz?{urllib.parse.urlencode(params)}"


def build_appmsg_url(mp_config: dict[str, Any], fakeid: str, begin: int, count: int) -> str:
    params = {
        "action": "list_ex",
        "begin": str(begin),
        "count": str(count),
        "fakeid": fakeid,
        "type": "9",
        "query": "",
        "token": mp_config["token"],
        "lang": "zh_CN",
        "f": "json",
        "ajax": "1",
    }
    return f"{WECHAT_MP_BASE}/appmsg?{urllib.parse.urlencode(params)}"


def request_headers(mp_config: dict[str, Any]) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Cookie": mp_config["cookie"],
        "Referer": "https://mp.weixin.qq.com/",
    }


def fetch_json(url: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WeChatMpDiscoveryError("微信后台返回的不是 JSON，登录态可能已失效。") from exc


def ensure_success(payload: dict[str, Any], action: str) -> None:
    base_resp = payload.get("base_resp") or {}
    ret = base_resp.get("ret", 0)
    if ret in {0, "0", None}:
        return
    err_msg = base_resp.get("err_msg") or payload.get("errmsg") or "unknown error"
    if str(ret) in {"200003", "200013", "-1"}:
        raise WeChatMpAuthError(f"微信后台接口 {action} 拒绝访问：{ret} {err_msg}")
    raise WeChatMpDiscoveryError(f"微信后台接口 {action} 返回错误：{ret} {err_msg}")


def parse_appmsg_item(item: dict[str, Any]) -> DiscoveredArticle:
    publish_ts = first_int(item, ["update_time", "create_time", "publish_time"])
    publish_time = None
    if publish_ts:
        publish_time = dt.datetime.fromtimestamp(publish_ts, CN_TZ).isoformat()
    return DiscoveredArticle(
        title=html.unescape(str(item.get("title") or "")).strip(),
        url=html.unescape(str(item.get("link") or "")).strip(),
        publish_ts=publish_ts,
        publish_time=publish_time,
        item_show_type=first_int(item, ["item_show_type"]),
        digest=html.unescape(str(item.get("digest") or "")).strip() or None,
        cover=html.unescape(str(item.get("cover") or item.get("cover_url") or "")).strip() or None,
        appmsgid=str(item.get("appmsgid") or "").strip() or None,
        aid=str(item.get("aid") or "").strip() or None,
    )


def first_int(item: dict[str, Any], keys: list[str]) -> int | None:
    for key in keys:
        value = item.get(key)
        if value in {"", None}:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def date_from_timestamp(timestamp: int | None) -> dt.date | None:
    if not timestamp:
        return None
    return dt.datetime.fromtimestamp(timestamp, CN_TZ).date()


def article_sort_key(article: DiscoveredArticle) -> tuple[int, str]:
    return (article.publish_ts or 0, article.url)


def dedupe_articles(articles: list[DiscoveredArticle]) -> list[DiscoveredArticle]:
    seen: set[str] = set()
    result: list[DiscoveredArticle] = []
    for article in articles:
        if article.url in seen:
            continue
        seen.add(article.url)
        result.append(article)
    return result


def normalize_text(value: str) -> str:
    return "".join(value.split())
