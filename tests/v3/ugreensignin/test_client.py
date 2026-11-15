"""绿联签到客户端的纯逻辑与页面流程测试。"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


PACKAGE_PATH = Path(__file__).resolve().parents[3] / "plugins.v3" / "ugreensignin"


def load(alias: str, filename: str):
    spec = importlib.util.spec_from_file_location(alias, PACKAGE_PATH / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


client = load("ugreen_client", "client.py")
message = load("ugreen_message", "message.py")

VALID_COOKIE = "6LQh_2132_saltkey=abc; 6LQh_2132_auth=secret; other=value"
TODAY = datetime.now().strftime("%Y-%m-%d")
CREDIT_TEXT = "\n".join([
    "积分：78",
    "积分: 78 (总积分=积分)",
    "积分记录",
    "操作 积分变更 详情 变更时间",
    f"每日签到\t积分 +1\t每日签到\t{TODAY} 00:56",
    f"积分规则\t积分 +2\t每天登录\t{TODAY} 00:27",
    "积分规则\t积分 +2\t每天登录\t2026-11-09 20:08",
])


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    def count(self):
        if "今日已签到" in self.selector:
            return int("今日已签到" in self.page.buttons)
        if "立即签到" in self.selector:
            return int("立即签到" in self.page.buttons)
        return 0

    def inner_text(self, timeout=5000):
        return self.page.text

    def click(self, timeout=90000):
        self.page.clicks += 1
        if self.page.succeed:
            self.page.buttons = {"今日已签到"}
            self.page.text = "签到打卡 已连续签到 3 天 今日已签到"


class FakePage:
    def __init__(self, state="ready", succeed=True):
        self.url = client.CHECKIN_URL
        self.succeed = succeed
        self.clicks = 0
        self.reloads = 0
        self.gotos = []
        self.buttons = {"今日已签到"} if state == "already" else {"立即签到"}
        self.text = "签到打卡 已连续签到 3 天 " + " ".join(self.buttons)
        if state == "login":
            self.text = "抱歉，您尚未登录，无法进行此操作"
            self.buttons = set()

    def locator(self, selector):
        return FakeLocator(self, selector)

    def set_default_timeout(self, value):
        pass

    def set_default_navigation_timeout(self, value):
        pass

    def goto(self, url, **kwargs):
        self.gotos.append(url)
        if "ac=credit" in url:
            self.text = CREDIT_TEXT

    def wait_for_function(self, *args, **kwargs):
        if not self.succeed:
            raise TimeoutError("no state change")

    def reload(self, *args, **kwargs):
        self.reloads += 1


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.cookies = []
        self.closed = False

    def add_cookies(self, cookies):
        self.cookies = cookies

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


def browser_modules(context):
    app = types.ModuleType("app")
    sdk = types.ModuleType("app.sdk")
    browser = types.ModuleType("app.sdk.browser")
    browser.launch_browser_context = lambda headless: context
    return {
        "app": app,
        "app.sdk": sdk,
        "app.sdk.browser": browser,
    }


def host_message_modules():
    """用宿主同名的数据结构替身，验证结构化通知的组装。"""
    from dataclasses import dataclass, field

    app = types.ModuleType("app")
    schemas = types.ModuleType("app.schemas")
    schemas.__path__ = []
    message_module = types.ModuleType("app.schemas.message")

    @dataclass
    class NotificationBlock:
        title: "str | None" = None
        text: "str | None" = None

    @dataclass
    class NotificationContent:
        title: "str | None" = None
        blocks: list = field(default_factory=list)

    message_module.NotificationBlock = NotificationBlock
    message_module.NotificationContent = NotificationContent
    schemas.message = message_module
    return {
        "app": app,
        "app.schemas": schemas,
        "app.schemas.message": message_module,
    }


class ClientTests(unittest.TestCase):
    def test_parse_cookie_string(self):
        cookies = client.parse_cookie_string("Cookie: " + VALID_COOKIE)
        self.assertEqual(3, len(cookies))
        self.assertEqual("club.ugnas.com", cookies[0]["domain"])
        self.assertEqual("6LQh_2132_auth", cookies[1]["name"])
        self.assertEqual("secret", cookies[1]["value"])

    def test_parse_cookie_rejects_missing_auth(self):
        with self.assertRaisesRegex(client.CookieExpiredError, "缺少登录凭证"):
            client.parse_cookie_string("session=abc")

    def test_parse_credit_text_reads_balance_and_today_gain(self):
        stats = client.parse_credit_text(CREDIT_TEXT, TODAY)
        self.assertEqual(78, stats.balance)
        self.assertEqual(1, stats.today_gain)
        self.assertEqual(3, stats.today_total)

    def test_parse_credit_text_skips_other_days(self):
        stats = client.parse_credit_text(CREDIT_TEXT, "2026-01-01")
        self.assertEqual(78, stats.balance)
        self.assertIsNone(stats.today_gain)
        self.assertIsNone(stats.today_total)

    def test_parse_credit_text_tolerates_unknown_layout(self):
        stats = client.parse_credit_text("页面改版了", TODAY)
        self.assertIsNone(stats.balance)
        self.assertIsNone(stats.today_gain)

    def test_parse_streak(self):
        self.assertEqual(12, client.parse_streak("已连续签到 12 天"))
        self.assertIsNone(client.parse_streak("签到打卡"))

    def test_already_signed_does_not_click(self):
        page = FakePage("already")
        context = FakeContext(page)
        with patch.dict(sys.modules, browser_modules(context)):
            result = client.UgreenCheckinClient().checkin(VALID_COOKIE)
        self.assertEqual("already", result.status)
        self.assertEqual(0, page.clicks)
        self.assertEqual(78, result.stats.balance)
        self.assertEqual(3, result.stats.streak)
        self.assertTrue(context.closed)

    def test_signin_success_is_verified_by_reload(self):
        page = FakePage("ready", succeed=True)
        context = FakeContext(page)
        with patch.dict(sys.modules, browser_modules(context)):
            result = client.UgreenCheckinClient().checkin(VALID_COOKIE)
        self.assertEqual("success", result.status)
        self.assertEqual(1, page.clicks)
        self.assertEqual(1, page.reloads)
        self.assertEqual(1, result.stats.today_gain)
        self.assertTrue(context.closed)

    def test_signin_rejects_login_page(self):
        page = FakePage("login")
        context = FakeContext(page)
        with patch.dict(sys.modules, browser_modules(context)):
            with self.assertRaises(client.CookieExpiredError):
                client.UgreenCheckinClient().checkin(VALID_COOKIE)
        self.assertEqual(0, page.clicks)
        self.assertTrue(context.closed)

    def test_unconfirmed_signin_fails_closed(self):
        page = FakePage("ready", succeed=False)
        context = FakeContext(page)
        with patch.dict(sys.modules, browser_modules(context)):
            with self.assertRaisesRegex(client.CheckinError, "未确认"):
                client.UgreenCheckinClient().checkin(VALID_COOKIE)
        self.assertEqual(1, page.reloads)
        self.assertTrue(context.closed)


class MessageTests(unittest.TestCase):
    SUCCESS = {
        "time": "2026-11-16 08:00:12",
        "status": "success",
        "message": "签到成功",
        "balance": 78,
        "today_gain": 1,
        "streak": 3,
    }

    def test_text_contains_points_and_streak(self):
        text = message.build_text(self.SUCCESS)
        self.assertIn("签到成功", text)
        self.assertIn("+1 积分", text)
        self.assertIn("78 积分", text)
        self.assertIn("3 天", text)
        self.assertIn("11-16 08:00", text)
        self.assertIn("再签 4 天可得 +5 积分", text)

    def test_text_omits_missing_points(self):
        text = message.build_text({
            "time": "2026-11-16 08:00:12",
            "status": "already",
            "message": "今日已签到",
        })
        self.assertIn("今日已签到", text)
        self.assertNotIn("积分", text)

    def test_text_for_failure_has_hint(self):
        text = message.build_text({
            "time": "2026-11-16 08:00:12",
            "status": "failed",
            "message": "论坛登录已失效，请更新 Cookie",
        })
        self.assertIn("签到失败", text)
        self.assertIn("论坛登录已失效", text)
        self.assertIn("立即运行一次", text)

    def test_title_carries_points(self):
        title, _, _ = message.build_notification(self.SUCCESS)
        self.assertIn("+1", title)
        self.assertIn("78", title)

    def test_rich_content_blocks(self):
        with patch.dict(sys.modules, host_message_modules()):
            rich = message.build_rich_content(self.SUCCESS)
        self.assertIsNotNone(rich)
        titles = [block.title for block in rich.blocks]
        self.assertIn("本次获得", titles)
        self.assertIn("积分余额", titles)
        for block in rich.blocks:
            value = block.text or ""
            for char in "*_-+":
                self.assertNotIn(char, value)


if __name__ == "__main__":
    unittest.main()
