"""绿联社区签到页面客户端。

只依赖 MoviePilot V3 提供的浏览器上下文，不持有账号密码，也不记录 Cookie。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Optional


CHECKIN_URL = "https://club.ugnas.com/plugin.php?id=ugreen_checkin"
CREDIT_URL = "https://club.ugnas.com/home.php?mod=spacecp&ac=credit"
COOKIE_DOMAIN = "club.ugnas.com"
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")

# 积分页顶部余额，形如「积分：78」「积分: 1,234」
_BALANCE_RE = re.compile(r"积分[：:]\s*([0-9][0-9,]*)")
# 签到页顶部连续天数，形如「已连续签到 1 天」
_STREAK_RE = re.compile(r"已连续签到\s*([0-9]+)\s*天")
# 积分记录表格行：操作 / 积分 +1 / 详情 / 2026-11-16 00:56
_CREDIT_ROW_RE = re.compile(
    r"^(?P<action>\S.*?)\s+积分\s*(?P<delta>[+-]\s*[0-9][0-9,]*)\s+"
    r"(?P<detail>.*?)\s+(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})\s+[0-9]{2}:[0-9]{2}$"
)


class CheckinError(Exception):
    """签到流程无法完成。"""


class CookieExpiredError(CheckinError):
    """Cookie 缺失、失效或未通过登录校验。"""


@dataclass(frozen=True)
class CheckinStats:
    """签到页顺带读到的积分信息，任何一项读不到都保持 None。"""

    balance: Optional[int] = None
    today_gain: Optional[int] = None
    today_total: Optional[int] = None
    streak: Optional[int] = None


@dataclass(frozen=True)
class CheckinResult:
    """一次签到的标准化结果。"""

    status: str
    message: str
    stats: CheckinStats = CheckinStats()


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


def parse_streak(text: str) -> Optional[int]:
    """从签到页正文读取连续签到天数。"""
    match = _STREAK_RE.search(text or "")
    return int(match.group(1)) if match else None


def parse_credit_text(text: str, today: Optional[str] = None) -> CheckinStats:
    """解析积分页正文，取当前余额与今日签到积分。

    页面是 Discuz 的积分记录表，顺带提供余额。表格结构一旦变化，这里只会
    返回 None 或部分字段，不影响签到本身的成败判断。
    """
    body = str(text or "")
    if not body:
        return CheckinStats()

    target_day = today or datetime.now().strftime("%Y-%m-%d")
    balance: Optional[int] = None
    balance_match = _BALANCE_RE.search(body)
    if balance_match:
        balance = _to_int(balance_match.group(1))

    today_gain = 0
    today_total = 0
    seen_gain = False
    seen_row = False
    for raw_line in body.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        row = _CREDIT_ROW_RE.match(line)
        if not row or row.group("date") != target_day:
            continue
        delta = _to_int(row.group("delta").replace(" ", ""))
        if delta is None:
            continue
        seen_row = True
        today_total += delta
        if "签到" in row.group("action") or "签到" in row.group("detail"):
            today_gain += delta
            seen_gain = True

    return CheckinStats(
        balance=balance,
        today_gain=today_gain if seen_gain else None,
        today_total=today_total if seen_row else None,
    )


def _to_int(value: Any) -> Optional[int]:
    """把带千分位的数字文本转成整数，失败返回 None。"""
    try:
        return int(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


class UgreenCheckinClient:
    """通过论坛当前签到页完成签到，并复核持久化状态与积分。"""

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
            if state == "unknown":
                raise CheckinError("未识别到签到按钮，论坛页面结构可能已变化")

            if state == "ready":
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
                if final_state == "login":
                    raise CookieExpiredError("签到过程中登录失效，请更新 Cookie")
                if final_state != "already":
                    raise CheckinError("点击后未确认签到成功，请稍后重试或检查 Cookie")
                status, message = "success", "签到成功"
            else:
                status, message = "already", "今日已签到"

            stats = self._read_stats(page)
            return CheckinResult(status, message, stats)
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

    def _read_stats(self, page: Any) -> CheckinStats:
        """读取连续天数、积分余额与今日签到积分；失败不影响签到结果。"""
        stats = CheckinStats()
        try:
            stats = replace(stats, streak=parse_streak(self._body_text(page)))
        except Exception:
            pass
        try:
            page.goto(CREDIT_URL, wait_until="domcontentloaded", timeout=self.timeout_ms)
            credit = parse_credit_text(self._body_text(page))
            stats = replace(
                stats,
                balance=credit.balance,
                today_gain=credit.today_gain,
                today_total=credit.today_total,
            )
        except Exception:
            pass
        return stats

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
