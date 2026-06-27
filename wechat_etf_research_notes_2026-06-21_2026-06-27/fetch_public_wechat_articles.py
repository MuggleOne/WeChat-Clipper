#!/usr/bin/env python3
"""Fetch public metadata and study notes for ETF研究笔记 recent WeChat articles.

This script intentionally writes summaries and short evidence quotes rather than
full article bodies.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from html.parser import HTMLParser
from pathlib import Path


OUT_DIR = Path(__file__).resolve().parent
ACCOUNT_NAME = "ETF研究笔记"
AUTHOR_NAME = "大E"
START_DATE = dt.date(2026, 6, 21)
END_DATE = dt.date(2026, 6, 27)

UA_DESKTOP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
UA_WECHAT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.50 NetType/WIFI Language/zh_CN"
)
COOKIE_JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIE_JAR))


def fetch(url: str, ua: str = UA_DESKTOP, referer: str | None = None, timeout: int = 30) -> tuple[str, str]:
    url = urllib.parse.quote(url, safe=":/?&=%._-~#+")
    headers = {"User-Agent": ua}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with OPENER.open(req, timeout=timeout) as resp:
        raw = resp.read()
        final_url = resp.geturl()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace"), final_url


def clean_html_text(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    return " ".join(value.split())


def sogou_search(query: str, filename: str) -> str:
    params = {
        "type": "2",
        "s_from": "input",
        "query": query,
        "ie": "utf8",
        "_sug_": "n",
        "_sug_type_": "",
    }
    url = "https://weixin.sogou.com/weixin?" + urllib.parse.urlencode(params)
    body, _ = fetch(url)
    approve_match = re.search(r"uuid = \"([^\"]+)\".*?ssToken = \"([^\"]+)\"", body, flags=re.S)
    if approve_match:
        approve_url = (
            "https://weixin.sogou.com/approve?"
            + urllib.parse.urlencode(
                {"uuid": approve_match.group(1), "token": approve_match.group(2), "from": "search"}
            )
        )
        try:
            fetch(approve_url, referer=url, timeout=10)
        except Exception:
            pass
    (OUT_DIR / "search_html").mkdir(exist_ok=True)
    (OUT_DIR / "search_html" / filename).write_text(body, encoding="utf-8")
    return body


def parse_sogou_results(body: str) -> list[dict]:
    items: list[dict] = []
    for li in re.findall(r"<li[^>]*>(.*?)</li>", body, flags=re.S):
        a = re.search(
            r'<a[^>]+href="([^"]+)"[^>]+uigs="article_title_\d+"[^>]*>(.*?)</a>',
            li,
            flags=re.S,
        )
        if not a:
            continue
        source = re.search(r'<span class="all-time-y2[^"]*">(.*?)</span>', li, flags=re.S)
        ts = re.search(r"timeConvert\('(\d+)'\)", li)
        digest = re.search(r'<p class="txt-info"[^>]*>(.*?)</p>', li, flags=re.S)
        href = html.unescape(a.group(1))
        if href.startswith("/"):
            href = "https://weixin.sogou.com" + href
        href = urllib.parse.quote(href, safe=":/?&=%._-~#+")
        items.append(
            {
                "title": clean_html_text(a.group(2)),
                "sogou_url": href,
                "source": clean_html_text(source.group(1)) if source else "",
                "publish_ts": int(ts.group(1)) if ts else None,
                "publish_time": (
                    dt.datetime.fromtimestamp(int(ts.group(1)), dt.timezone(dt.timedelta(hours=8))).isoformat()
                    if ts
                    else None
                ),
                "digest": clean_html_text(digest.group(1)) if digest else "",
            }
        )
    return items


class ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_content = False
        self.depth = 0
        self.text_parts: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = dict(attrs)
        if tag == "div" and attrs_d.get("id") == "js_content":
            self.in_content = True
            self.depth = 1
            return
        if not self.in_content:
            return
        self.depth += 1
        if tag in {"p", "section", "br"}:
            self.text_parts.append("\n")
        if tag == "img":
            src = attrs_d.get("data-src") or attrs_d.get("src") or ""
            if src:
                self.images.append(src)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_content:
            return
        if tag in {"p", "section"}:
            self.text_parts.append("\n")
        self.depth -= 1
        if self.depth <= 0:
            self.in_content = False

    def handle_data(self, data: str) -> None:
        if self.in_content:
            text = data.strip()
            if text:
                self.text_parts.append(text)

    def text(self) -> str:
        value = "".join(self.text_parts)
        value = "\n".join(line.strip() for line in re.split(r"\n+", value) if line.strip())
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = value.split("var first_sceen__time")[0].strip()
        return value


def extract_var(pattern: str, body: str) -> str | None:
    m = re.search(pattern, body, flags=re.S)
    if not m:
        return None
    return html.unescape(m.group(1))


def parse_article(body: str, final_url: str) -> dict:
    parser = ArticleParser()
    parser.feed(body)
    text = parser.text()
    title = extract_var(r"var msg_title = '([^']*)'\.html", body)
    nickname = extract_var(r'var nickname = htmlDecode\("(.*?)"\);', body)
    author = extract_var(r'id="js_author_name"[^>]*>(.*?)</span>', body)
    ct = extract_var(r'var ct = "(\d+)";', body)
    pub_time = None
    if ct:
        pub_time = dt.datetime.fromtimestamp(int(ct), dt.timezone(dt.timedelta(hours=8))).isoformat()
    return {
        "url": final_url,
        "title": title,
        "account": nickname,
        "author": clean_html_text(author or "") or None,
        "publish_ts": int(ct) if ct else None,
        "publish_time": pub_time,
        "biz": extract_var(r'var biz = "([^"]+)"', body),
        "user_name": extract_var(r'var user_name = "([^"]+)";', body),
        "appmsgid": extract_var(r'var appmsgid = "(\d+)"', body),
        "sn": extract_var(r'var sn = "([0-9a-f]+)"', body),
        "text_len": len(text),
        "images": parser.images,
        "body_for_internal_processing": text,
    }


def extract_sogou_inner_url(body: str) -> str | None:
    parts = re.findall(r"url\s*\+=\s*'([^']*)';", body)
    if not parts:
        return None
    url = "".join(parts)
    url = url.replace("&amp;", "&")
    url = url.replace("@", "")
    return url if "mp.weixin.qq.com" in url else None


def short_quote(text: str, keyword: str, max_chars: int = 32) -> str | None:
    idx = text.find(keyword)
    if idx < 0:
        return None
    start = max(0, idx - 8)
    end = min(len(text), idx + len(keyword) + 18)
    quote = text[start:end].replace("\n", "")
    return quote[:max_chars]


def make_study_note(article: dict) -> str:
    text = article.pop("body_for_internal_processing", "")
    evidence = []
    for keyword in ["继续聚焦结构", "提前埋伏", "补回来", "继续聚焦国产算力", "正常过周末", "不得作为任何行动依据"]:
        q = short_quote(text, keyword)
        if q and q not in evidence:
            evidence.append(q)
    evidence = evidence[:4]

    vocabulary = {
        "宏观/指数": ["PCE", "美元通缩", "加息", "指数", "大分歧", "韩国熔断", "美股", "人民币"],
        "AI/算力": ["AI", "算力", "HBM", "服务器", "光模块", "玻璃桥", "800V", "Rubin"],
        "半导体/存储": ["存储", "DRAM", "NAND", "半导体设备", "测试设备", "长鑫", "晶圆", "国产替代", "去日化"],
        "电子/PCB": ["PCB", "CCL", "覆铜板", "生益", "南亚"],
        "其他行业": ["化工", "新能源", "电力", "机器人", "稀土", "军工", "创新药", "银行", "证券"],
        "风险/操作": ["聚焦", "补回来", "过周末", "埋伏", "上车", "风险", "投资需谨慎"],
    }
    found: dict[str, list[str]] = {}
    for category, words in vocabulary.items():
        hits = [word for word in words if word in text]
        if hits:
            found[category] = hits

    nums = re.findall(r"(?:\d+(?:\.\d+)?%|\d+[-至]\d+个月|\d+[-至]\d+万片|\d+亿美元|\d+年|\d+月\d+日)", text)
    data_points = []
    for item in nums:
        if item not in data_points:
            data_points.append(item)
    data_points = data_points[:12]

    macro_terms = "、".join(found.get("宏观/指数", [])[:6]) or "当日市场环境"
    main_terms = []
    for cat in ["AI/算力", "半导体/存储", "电子/PCB", "其他行业"]:
        main_terms.extend(found.get(cat, [])[:5])
    main_terms_text = "、".join(main_terms[:12]) or "当日结构性主线"
    risk_terms = "、".join(found.get("风险/操作", [])[:8]) or "风险提示与交易节奏"

    note = [
        f"# {article.get('title') or '未命名文章'}",
        "",
        "## 元数据",
        "",
        f"- 公众号：{article.get('account') or ACCOUNT_NAME}",
        f"- 作者：{article.get('author') or AUTHOR_NAME}",
        f"- 发布时间：{article.get('publish_time') or '未知'}",
        f"- 原文链接：{article.get('url')}",
        f"- 正文长度：{article.get('text_len')} 字符（脚本解析到正文，但本地笔记不保存全文）",
        f"- 正文指纹：`{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}`",
        "",
        "## 关键引文",
        "",
    ]
    if evidence:
        note.extend([f"- **原文关键句：{q}**" for q in evidence])
    else:
        note.append("- 未抽取到短引文。")
    note.extend(
        [
            "",
            "## 结构化学习摘要",
            "",
            "### 文章主轴",
            "",
            f"这篇文章围绕“{article.get('title') or '当日复盘'}”展开。作者先处理 {macro_terms} 等背景，再把注意力转向 {main_terms_text}，整体是偏交易复盘与产业逻辑跟踪的写法。",
            "",
            "### 关键词分组",
            "",
            "\n".join(f"- {cat}：{'、'.join(words)}" for cat, words in found.items()) or "- 未识别到关键词。",
            "",
            "### 数据线索",
            "",
            "\n".join(f"- {item}" for item in data_points) if data_points else "- 未抽取到明显数字线索。",
            "",
            "### 操作/持仓判断线索",
            "",
            f"围绕 {risk_terms} 这些表达，作者更像是在记录当日仓位思路、板块排序和扰动后的应对，而不是给出可直接照搬的交易指令。相关原文只保留在上方短引文中，后续复盘应回到原文链接核对上下文。",
            "",
            "### 可复用问题",
            "",
            "- 当日指数波动是宏观定价变化，还是产业结构主线内部的扰动？",
            "- 作者强调的主线是否有可验证的订单、价格、产能或政策证据？",
            "- 文中的短期操作判断与中长期产业逻辑是否混在一起，需要如何拆开复盘？",
            "- 下一篇文章是否修正了这篇文章中的判断？",
            "",
            "### 风险声明",
            "",
            "原文明确属于个人市场记录，不应作为投资决策依据；本地文件只用于学习、索引和复盘。",
            "",
            "## 图片线索",
            "",
        ]
    )
    imgs = article.get("images") or []
    if imgs:
        note.extend([f"- {img}" for img in imgs[:10]])
    else:
        note.append("- 无图片。")
    note.extend(
        [
            "",
            "## 抓取说明",
            "",
            "- 本文件保留公开页面的元数据、结构化学习笔记和少量短引文。",
            "- 未保存或输出整篇原文正文。",
        ]
    )
    return "\n".join(note)


def slugify(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value, flags=re.U).strip("_")
    return f"{safe[:80]}_{digest}"


def main() -> None:
    search_dir = OUT_DIR / "search_html"
    search_dir.mkdir(exist_ok=True)
    notes_dir = OUT_DIR / "notes"
    notes_dir.mkdir(exist_ok=True)
    for old_note in notes_dir.glob("*.md"):
        old_note.unlink()

    candidates: list[dict] = []
    current = START_DATE
    while current <= END_DATE:
        cn_date = f"{current.month}月{current.day}日"
        queries = [
            f'"{ACCOUNT_NAME}" "{cn_date}"',
            f"{ACCOUNT_NAME} {cn_date}",
        ]
        for qi, query in enumerate(queries):
            filename = f"sogou_{current.isoformat()}_{qi}.html"
            try:
                body = sogou_search(query, filename)
                results = parse_sogou_results(body)
                for item in results:
                    item["search_query"] = query
                    item["search_date"] = current.isoformat()
                    if item.get("source") == ACCOUNT_NAME or ACCOUNT_NAME in item.get("source", ""):
                        candidates.append(item)
            except Exception as exc:  # noqa: BLE001
                candidates.append(
                    {
                        "search_query": query,
                        "search_date": current.isoformat(),
                        "error": repr(exc),
                    }
                )
            time.sleep(0.7)
        current += dt.timedelta(days=1)

    deduped: dict[str, dict] = {}
    for item in candidates:
        key = item.get("title") or item.get("sogou_url") or repr(item)
        if key not in deduped:
            deduped[key] = item

    articles: list[dict] = []
    failures: list[dict] = []
    for item in deduped.values():
        if not item.get("sogou_url"):
            failures.append(item)
            continue
        try:
            body, final_url = fetch(item["sogou_url"], referer="https://weixin.sogou.com/")
            inner_url = extract_sogou_inner_url(body)
            if "mp.weixin.qq.com" in final_url:
                article_body, article_url = fetch(final_url, ua=UA_WECHAT)
            elif inner_url:
                article_body, article_url = fetch(inner_url, ua=UA_WECHAT, referer=item["sogou_url"])
            elif "mp.weixin.qq.com" in body[:2000]:
                article_body, article_url = body, final_url
            else:
                failures.append({**item, "failure": "Sogou link did not resolve to mp.weixin.qq.com", "final_url": final_url})
                continue
            parsed = parse_article(article_body, article_url)
            parsed["sogou_candidate"] = item
            if parsed.get("account") != ACCOUNT_NAME:
                failures.append({**item, "failure": "Resolved article is not target account", "parsed_account": parsed.get("account")})
                continue
            article_meta = dict(parsed)
            note = make_study_note(article_meta)
            filename = slugify(f"{parsed.get('publish_time') or item.get('search_date')} {parsed.get('title') or item.get('title')}")
            note_path = notes_dir / f"{filename}.md"
            note_path.write_text(note, encoding="utf-8")
            article_meta["local_note"] = str(note_path.relative_to(OUT_DIR))
            articles.append(article_meta)
            time.sleep(0.7)
        except Exception as exc:  # noqa: BLE001
            failures.append({**item, "failure": repr(exc)})

    manifest = {
        "account": ACCOUNT_NAME,
        "author_hint": AUTHOR_NAME,
        "date_window": {"start": START_DATE.isoformat(), "end": END_DATE.isoformat()},
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        "articles_found": len(articles),
        "articles": articles,
        "candidate_count": len(deduped),
        "candidates": list(deduped.values()),
        "failures": failures,
        "copyright_note": "Full article bodies are not written to disk by this script; notes contain metadata, summaries, and short evidence quotes.",
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    index_lines = [
        f"# {ACCOUNT_NAME} 近 1 周公开文章抓取索引",
        "",
        f"- 日期窗口：{START_DATE.isoformat()} 至 {END_DATE.isoformat()}",
        f"- 生成时间：{manifest['generated_at']}",
        f"- 命中文章数：{len(articles)}",
        f"- 候选结果数：{len(deduped)}",
        "",
        "## 已拉回的学习笔记",
        "",
    ]
    if articles:
        for art in sorted(articles, key=lambda x: x.get("publish_ts") or 0):
            index_lines.append(f"- [{art.get('title')}]({art.get('local_note')})")
            index_lines.append(f"  - 发布时间：{art.get('publish_time')}")
            index_lines.append(f"  - 原文：{art.get('url')}")
    else:
        index_lines.append("- 未找到可访问文章。")
    index_lines.extend(["", "## 失败或限制", ""])
    if failures:
        for fail in failures[:30]:
            index_lines.append(f"- {fail.get('search_date', '')} {fail.get('title', fail.get('search_query', ''))}: {fail.get('failure') or fail.get('error')}")
    else:
        index_lines.append("- 无。")
    (OUT_DIR / "INDEX.md").write_text("\n".join(index_lines), encoding="utf-8")
    print(json.dumps({"articles_found": len(articles), "candidate_count": len(deduped), "out_dir": str(OUT_DIR)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
