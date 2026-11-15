"""绿联签到客户端的纯逻辑与页面流程测试。"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


CLIENT_PATH = (
    Path(__file__).resolve().parents[3]
    / "plugins.v3"
    / "ugreensignin"
    / "client.py"
)
SPEC = importlib.util.spec_from_file_location("ugreen_client", CLIENT_PATH)
client = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = client
SPEC.loader.exec_module(client)

VALID_COOKIE = "6LQh_2132_saltkey=abc; 6LQh_2132_auth=secret; other=value"


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
            self.page.text = "签到打卡 今日已签到"


class FakePage:
    def __init__(self, state="ready", succeed=True):
        self.url = client.CHECKIN_URL
        self.succeed = succeed
        self.clicks = 0
        self.reloads = 0
        self.buttons = {"今日已签到"} if state == "already" else {"立即签到"}
        self.text = "签到打卡 " + " ".join(self.buttons)
        if state == "login":
            self.text = "抱歉，您尚未登录，无法进行此操作"
            self.buttons = set()

    def locator(self, selector):
        return FakeLocator(self, selector)

    def set_default_timeout(self, value):
        pass

    def set_default_navigation_timeout(self, value):
        pass

    def goto(self, *args, **kwargs):
        pass

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

    def test_already_signed_does_not_click(self):
        page = FakePage("already")
        context = FakeContext(page)
        with patch.dict(sys.modules, browser_modules(context)):
            result = client.UgreenCheckinClient().checkin(VALID_COOKIE)
        self.assertEqual("already", result.status)
        self.assertEqual(0, page.clicks)
        self.assertTrue(context.closed)

    def test_signin_success_is_verified_by_reload(self):
        page = FakePage("ready", succeed=True)
        context = FakeContext(page)
        with patch.dict(sys.modules, browser_modules(context)):
            result = client.UgreenCheckinClient().checkin(VALID_COOKIE)
        self.assertEqual("success", result.status)
        self.assertEqual(1, page.clicks)
        self.assertEqual(1, page.reloads)
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


if __name__ == "__main__":
    unittest.main()
