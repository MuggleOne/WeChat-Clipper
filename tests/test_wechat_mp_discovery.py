from __future__ import annotations

import datetime as dt
import sys
import unittest
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from wechat_mp_discovery import (  # noqa: E402
    CN_TZ,
    WeChatMpAuthError,
    discover_account_articles,
    normalized_mp_config,
)


def timestamp(year: int, month: int, day: int) -> int:
    return int(dt.datetime(year, month, day, 8, 0, tzinfo=CN_TZ).timestamp())


class WeChatMpDiscoveryTest(unittest.TestCase):
    def test_discover_resolves_fakeid_filters_dates_and_sorts(self) -> None:
        config = {
            "wechat_mp": {
                "cookie": "fake_cookie",
                "token": "fake_token",
                "page_size": 2,
                "max_pages": 3,
                "request_delay_seconds": 0,
            }
        }

        def fake_fetch(url: str, headers: dict[str, str], timeout: int) -> dict[str, object]:
            self.assertIn("fake_cookie", headers["Cookie"])
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path.endswith("/searchbiz"):
                return {
                    "base_resp": {"ret": 0},
                    "list": [
                        {"nickname": "Other Account", "fakeid": "other"},
                        {"nickname": "Sample Account", "fakeid": "sample_fakeid"},
                    ],
                }
            self.assertEqual(query["fakeid"][0], "sample_fakeid")
            begin = int(query["begin"][0])
            if begin == 0:
                return {
                    "base_resp": {"ret": 0},
                    "app_msg_list": [
                        {
                            "title": "Newer",
                            "link": "https://mp.weixin.qq.com/s/newer",
                            "update_time": timestamp(2026, 6, 30),
                        },
                        {
                            "title": "Older In Range",
                            "link": "https://mp.weixin.qq.com/s/older",
                            "update_time": timestamp(2026, 6, 27),
                        },
                    ],
                }
            return {
                "base_resp": {"ret": 0},
                "app_msg_list": [
                    {
                        "title": "Too Old",
                        "link": "https://mp.weixin.qq.com/s/too-old",
                        "update_time": timestamp(2026, 6, 20),
                    }
                ],
            }

        fakeid, articles = discover_account_articles(
            config,
            account_name="Sample Account",
            from_date=dt.date(2026, 6, 25),
            to_date=dt.date(2026, 6, 30),
            fetch_json_func=fake_fetch,
            sleep_func=lambda seconds: None,
        )

        self.assertEqual(fakeid, "sample_fakeid")
        self.assertEqual([article.title for article in articles], ["Older In Range", "Newer"])
        self.assertEqual(articles[0].publish_time, "2026-06-27T08:00:00+08:00")

    def test_missing_login_state_raises_auth_error(self) -> None:
        with self.assertRaises(WeChatMpAuthError):
            normalized_mp_config({"wechat_mp": {"cookie": "", "token": "fake_token"}})


if __name__ == "__main__":
    unittest.main()
