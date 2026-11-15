"""绿联社区签到页面客户端。

只依赖 MoviePilot V3 提供的浏览器上下文，不持有账号密码，也不记录 Cookie。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


CHECKIN_URL = "https://club.ugnas.com/plugin.php?id=ugreen_checkin"
COOKIE_DOMAIN = "club.ugnas.com"
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class CheckinError(Exception):
    """签到流程无法完成。"""


class CookieExpiredError(CheckinError):
    """Cookie 缺失、失效或未通过登录校验。"""


@dataclass(frozen=True)
class CheckinResult:
    """一次签到的标准化结果。"""

    status: str
    message: str


def parse_cookie_string(raw: str) -> list[dict[str, Any]]:
    """把请求头形式的 Cookie 转为 Playwright Cookie 列表。

    只接受 club.ugnas.com 的键值对，不保留过期时间，避免把浏览器侧旧会话
    的过期属性错误地带入新上下文。
    """
    text = str(raw or "").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        raise CookieExpiredError("请先配置论坛 Cookie")

    cookies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in text.split(";"):
        name, separator, value = item.strip().partition("=")
        if not separator or not name or not _COOKIE_NAME_RE.fullmatch(name):
            continue
        if name in seen:
            continue
        seen.add(name)
        cookies.append({
            "name": name,
            "value": value.strip(),
            "domain": COOKIE_DOMAIN,
            "path": "/",
            "secure": True,
            "httpOnly": False,
        })

    if not cookies:
        raise CookieExpiredError("Cookie 格式无效，请复制完整请求头 Cookie")
    if not any(item["name"].lower().endswith("_auth") for item in cookies):
        raise CookieExpiredError("Cookie 中缺少登录凭证 *_auth，请重新复制完整 Cookie")
    return cookies


class UgreenCheckinClient:
    """通过论坛当前签到页完成签到并复核持久化状态。"""

    def __init__(self, timeout: int = 90) -> None:
        self.timeout = max(30, min(int(timeout), 180))
        self.timeout_ms = self.timeout * 1000

    def checkin(self, raw_cookie: str) -> CheckinResult:
        """执行签到。成功后重新加载页面，避免把按钮动画误判为成功。"""
        cookies = parse_cookie_string(raw_cookie)
        from app.sdk.browser import launch_browser_context

        context = None
        try:
            context = launch_browser_context(headless=True)
            context.add_cookies(cookies)
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)
            page.set_default_navigation_timeout(self.timeout_ms)
            page.goto(CHECKIN_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)

            state = self._read_state(page)
            if state == "login":
                raise CookieExpiredError("论坛登录已失效，请更新 Cookie")
            if state == "already":
                return CheckinResult("already", "今日已签到")
            if state != "ready":
                raise CheckinError("未识别到签到按钮，论坛页面结构可能已变化")

            page.locator('button:has-text("立即签到")').first.click(timeout=self.timeout_ms)
            try:
                page.wait_for_function(
                    """() => {
                        const text = document.body ? document.body.innerText : '';
                        return text.includes('今日已签到') || text.includes('签到成功');
                    }""",
                    timeout=self.timeout_ms,
                )
            except Exception:
                # 请求可能已在服务端完成。无论前端是否及时回调，都 reload 复核。
                pass

            page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
            final_state = self._read_state(page)
            if final_state == "already":
                return CheckinResult("success", "签到成功")
            if final_state == "login":
                raise CookieExpiredError("签到过程中登录失效，请更新 Cookie")
            raise CheckinError("点击后未确认签到成功，请稍后重试或检查 Cookie")
        except (CookieExpiredError, CheckinError):
            raise
        except Exception as err:
            raise CheckinError(f"浏览器签到异常：{err}") from err
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass

    def _read_state(self, page: Any) -> str:
        """识别登录失效、已签到、可签到和未知页面四种状态。"""
        url = str(getattr(page, "url", "") or "")
        if "member.php?mod=logging" in url or "web.ugnas.com" in url:
            return "login"

        text = self._body_text(page)
        if "您尚未登录" in text or "抱歉，您尚未登录" in text:
            return "login"
        if self._button_exists(page, "今日已签到") or "今日已签到" in text:
            return "already"
        if self._button_exists(page, "立即签到"):
            return "ready"
        return "unknown"

    @staticmethod
    def _button_exists(page: Any, text: str) -> bool:
        try:
            return page.locator(f'button:has-text("{text}")').count() > 0
        except Exception:
            return False

    @staticmethod
    def _body_text(page: Any) -> str:
        try:
            return str(page.locator("body").inner_text(timeout=5000) or "")
        except Exception:
            try:
                return str(page.content() or "")
            except Exception:
                return ""
