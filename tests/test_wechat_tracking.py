from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from wechat_tracking import (  # noqa: E402
    classify_article,
    empty_state,
    load_state,
    record_article_version,
    save_state,
    state_path,
    subscriptions_for,
    tracking_window,
    upsert_subscription,
)


class WeChatTrackingTest(unittest.TestCase):
    def test_classify_article_creates_skips_and_versions_by_sha(self) -> None:
        state = empty_state()

        first = classify_article(state, "https://mp.weixin.qq.com/s/sample?nwr_flag=1", "sha1")
        self.assertEqual(first["action"], "create")
        self.assertFalse(first["force_create"])

        record_article_version(
            state,
            url="https://mp.weixin.qq.com/s/sample",
            title="Sample",
            account="Sample Account",
            author="Author",
            publish_time="2026-06-27T08:00:00+08:00",
            content_sha256="sha1",
            version=1,
            source_markdown="wechat_article_outputs/sample.md",
            notion_page_url="https://notion.example/page",
            obsidian_markdown="/vault/Sample.md",
        )

        unchanged = classify_article(state, "https://mp.weixin.qq.com/s/sample#wechat_redirect", "sha1")
        self.assertEqual(unchanged["action"], "skip")

        changed = classify_article(state, "https://mp.weixin.qq.com/s/sample", "sha2")
        self.assertEqual(changed["action"], "create_version")
        self.assertTrue(changed["force_create"])
        self.assertEqual(changed["version"], 2)
        self.assertEqual(changed["title_suffix"], "v2")

    def test_tracking_window_uses_default_and_backfill_start(self) -> None:
        today = dt.date(2026, 6, 28)

        self.assertEqual(
            tracking_window("daily", "", today_date=today),
            (dt.date(2026, 6, 27), today),
        )
        self.assertEqual(
            tracking_window("weekly", "2026-06-01T08:00:00+08:00", today_date=today),
            (dt.date(2026, 5, 31), today),
        )
        self.assertEqual(
            tracking_window("monthly", "2026-06-20T08:00:00+08:00", today_date=today),
            (dt.date(2026, 5, 25), today),
        )

    def test_subscription_and_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = {"tracking": {"state_path": str(Path(tmp) / "state.json")}}
            state = empty_state()
            subscription = upsert_subscription(
                state,
                name="Sample",
                frequency="weekly",
                seed_url="https://mp.weixin.qq.com/s/sample?nwr_flag=1",
                account="Sample Account",
                biz="biz",
                user_name="gh_sample",
                fakeid="fakeid",
            )
            save_state(config, state)
            loaded = load_state(config)

        self.assertEqual(state_path(config), Path(tmp) / "state.json")
        self.assertEqual(subscription["seed_url"], "https://mp.weixin.qq.com/s/sample")
        self.assertEqual(len(subscriptions_for(loaded, frequency="weekly")), 1)
        self.assertEqual(json.loads(json.dumps(loaded))["subscriptions"][0]["fakeid"], "fakeid")


if __name__ == "__main__":
    unittest.main()
