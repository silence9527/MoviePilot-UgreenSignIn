"""绿联社区签到与积分采集客户端。

纯标准库实现，不依赖浏览器，也不读取系统代理设置：
- HTTP 请求走 urllib，显式传入空的 ProxyHandler，任何环境变量里的代理都不会生效；
- Cookie 只用于本次请求，不落盘、不写日志。
"""

from __future__ import annotations

import gzip
import http.cookiejar
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

BASE_URL = "https://club.ugnas.com"
CHECKIN_PATH = "/plugin.php?id=ugreen_checkin"
CHECKIN_SIGN_PATH = "/plugin.php?id=ugreen_checkin&action=sign"
CREDIT_PATH = "/home.php?mod=spacecp&ac=credit"
RULE_PATH = "/home.php?mod=spacecp&ac=credit&op=rule"
COOKIE_DOMAIN = "club.ugnas.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
)

_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_FORMHASH_RE = re.compile(r"formhash=([0-9a-z]{6,16})")
_STREAK_RE = re.compile(r"已连续签到\s*([0-9]+)\s*天")
_BALANCE_RE = re.compile(r"积分[：:]\s*([0-9][0-9,]*)")
_TR_RE = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_TD_RE = re.compile(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style).*?</\1>")
_DETAIL_FID_RE = re.compile(r"\s*[（(]fid[:：]\s*\d+[）)]\s*")


class CheckinError(Exception):
    """签到流程无法完成。"""


class CookieExpiredError(CheckinError):
    """Cookie 缺失、失效或未通过登录校验。"""


@dataclass(frozen=True)
class ActionStat:
    """官方积分规则里的一项动作，以及今日的完成情况。"""

    name: str
    limit: Optional[int] = None
    reward: Optional[int] = None
    count: int = 0
    points: int = 0

    @property
    def done(self) -> bool:
        return self.count > 0

    @property
    def full(self) -> bool:
        return self.limit is not None and self.count >= self.limit

    @property
    def remain(self) -> Optional[int]:
        if self.limit is None:
            return None
        return max(0, self.limit - self.count)


@dataclass(frozen=True)
class CheckinStats:
    """签到顺带采集到的积分信息，读不到的字段保持 None。"""

    balance: Optional[int] = None
    today_gain: Optional[int] = None
    today_total: Optional[int] = None
    streak: Optional[int] = None
    actions: Tuple[ActionStat, ...] = ()
    daily_cap: Optional[int] = None

    @property
    def today_missing(self) -> Optional[int]:
        if self.daily_cap is None or self.today_total is None:
            return None
        return max(0, self.daily_cap - self.today_total)


@dataclass(frozen=True)
class CheckinResult:
    """一次签到的标准化结果。"""

    status: str
    message: str
    stats: CheckinStats = field(default_factory=CheckinStats)


def normalize_cookie(raw: str) -> str:
    """校验并规范化请求头形式的 Cookie 字符串。"""
    text = str(raw or "").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        raise CookieExpiredError("请先配置论坛 Cookie")

    pairs: List[str] = []
    seen: set[str] = set()
    for item in text.split(";"):
        name, separator, value = item.strip().partition("=")
        if not separator or not name or not _COOKIE_NAME_RE.fullmatch(name):
            continue
        if name in seen:
            continue
        seen.add(name)
        pairs.append(f"{name}={value.strip()}")
    if not pairs:
        raise CookieExpiredError("Cookie 格式无效，请复制完整请求头 Cookie")
    if not any(item.split("=", 1)[0].lower().endswith("_auth") for item in pairs):
        raise CookieExpiredError("Cookie 中缺少登录凭证 *_auth，请重新复制完整 Cookie")
    return "; ".join(pairs)


def build_cookie_jar(raw: str) -> http.cookiejar.CookieJar:
    """把 Cookie 字符串塞进标准库 CookieJar。"""
    jar = http.cookiejar.CookieJar()
    for item in normalize_cookie(raw).split("; "):
        name, _, value = item.partition("=")
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=COOKIE_DOMAIN, domain_specified=True, domain_initial_dot=False,
            path="/", path_specified=True, secure=True, expires=None,
            discard=False, comment=None, comment_url=None, rest={},
        ))
    return jar


def strip_html(html: str) -> str:
    """去掉脚本、样式与标签，得到按行排列的纯文本。"""
    text = _SCRIPT_RE.sub("\n", str(html or ""))
    text = _TR_RE.sub(lambda m: "\n" + m.group(1) + "\n", text)
    text = _TAG_RE.sub("\n", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"&lt;", "<", text).replace("&gt;", ">").replace("&quot;", '"')
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def parse_formhash(html: str) -> Optional[str]:
    match = _FORMHASH_RE.search(str(html or ""))
    return match.group(1) if match else None


def parse_checkin_state(html: str) -> str:
    """识别登录失效、已签到、可签到、未知四种状态。"""
    text = strip_html(html)
    if "您尚未登录" in text or "抱歉，您尚未登录" in text:
        return "login"
    if "今日已签到" in text:
        return "already"
    if "立即签到" in str(html or ""):
        return "ready"
    return "unknown"


def parse_streak(html: str) -> Optional[int]:
    match = _STREAK_RE.search(strip_html(html))
    return int(match.group(1)) if match else None


def parse_balance(html: str) -> Optional[int]:
    match = _BALANCE_RE.search(strip_html(html))
    return _to_int(match.group(1)) if match else None


def parse_credit_rows(html: str) -> List[Dict[str, str]]:
    """解析积分记录表，返回 操作/变更/详情/时间 四列的原始文本。"""
    rows: List[Dict[str, str]] = []
    for raw in _TR_RE.findall(str(html or "")):
        cells = [_clean(cell) for cell in _TD_RE.findall(raw)]
        if len(cells) < 4:
            continue
        delta = _to_int(cells[1].replace("积分", ""))
        if delta is None:
            continue
        rows.append({
            "action": cells[0],
            "delta": str(delta),
            "detail": cells[2],
            "time": cells[3],
        })
    return rows


def parse_rules(html: str) -> List[Tuple[str, Optional[int], Optional[int]]]:
    """解析积分规则表，返回 (动作名称, 每日上限次数, 单次奖励)。

    规则页的列是：动作名称 / 周期范围 / 周期内最多奖励次数 / 积分。
    只有「每天」周期且次数可数的动作才有上限，其余返回 None。
    """
    rules: List[Tuple[str, Optional[int], Optional[int]]] = []
    for raw in _TR_RE.findall(str(html or "")):
        cells = [_clean(cell) for cell in _TD_RE.findall(raw)]
        if len(cells) < 4:
            continue
        name, period, times, reward = cells[0], cells[1], cells[2], cells[3]
        if not name or "动作" in name:
            continue
        limit = _to_int(times)
        points = _to_int(reward)
        if points is None:
            continue
        if "每天" not in period:
            limit = None
        rules.append((name, limit, points))
    return rules


def build_action_stats(
    rules: List[Tuple[str, Optional[int], Optional[int]]],
    rows: List[Dict[str, str]],
    today: Optional[str] = None,
) -> Tuple[ActionStat, ...]:
    """把今日积分记录按官方动作归类，缺省动作补 0。"""
    target = today or datetime.now().strftime("%Y-%m-%d")
    counted: Dict[str, Tuple[int, int]] = {}
    for row in rows:
        if not str(row.get("time", "")).startswith(target):
            continue
        name = _DETAIL_FID_RE.sub("", row.get("detail") or row.get("action") or "").strip()
        delta = _to_int(row.get("delta"))
        if not name or delta is None:
            continue
        count, points = counted.get(name, (0, 0))
        counted[name] = (count + 1, points + delta)

    stats: List[ActionStat] = []
    for name, limit, reward in rules:
        count, points = counted.pop(name, (0, 0))
        stats.append(ActionStat(name=name, limit=limit, reward=reward,
                                count=count, points=points))
    for name, (count, points) in counted.items():
        stats.append(ActionStat(name=name, count=count, points=points))
    # 签到是插件自己完成的任务，放到最前面更直观。
    stats.sort(key=lambda item: 0 if "签到" in item.name else 1)
    return tuple(stats)


def daily_cap(rules: List[Tuple[str, Optional[int], Optional[int]]]) -> Optional[int]:
    """官方规则下每天主动能拿到的分数上限。

    「文章被评论」「加精华」这类被动动作不计入上限，否则会高估目标值。
    """
    total = 0
    seen = False
    for name, limit, reward in rules:
        if limit is None or reward is None or "被评论" in name or "精华" in name:
            continue
        total += limit * reward
        seen = True
    return total if seen else None


class UgreenClient:
    """用 HTTP 完成签到并采集积分，全程不使用代理。"""

    def __init__(self, timeout: int = 30, transport: Any = None) -> None:
        self.timeout = max(10, min(int(timeout), 120))
        self._jar: Optional[http.cookiejar.CookieJar] = None
        self._opener: Any = None
        # 测试时可注入 transport(path, data, ajax) -> (status, text, final_url)
        self._transport = transport

    def _build_opener(self, raw_cookie: str) -> Any:
        self._jar = build_cookie_jar(raw_cookie)
        # 关键：ProxyHandler({}) 让 urllib 完全忽略 HTTP_PROXY / HTTPS_PROXY 等环境变量。
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self._jar),
            urllib.request.HTTPRedirectHandler(),
        )

    def _request(self, path: str, data: Optional[Dict[str, str]] = None,
                 ajax: bool = False) -> Tuple[int, str, str]:
        if self._transport is not None:
            return self._transport(path, data, ajax)
        url = path if path.startswith("http") else BASE_URL + path
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Referer"] = BASE_URL + CHECKIN_PATH
        request = urllib.request.Request(url, data=body, headers=headers)
        with self._opener.open(request, timeout=self.timeout) as response:
            raw = response.read()
            final_url = response.geturl()
            status = getattr(response, "status", 200)
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return status, raw.decode("utf-8", errors="ignore"), final_url

    @staticmethod
    def _is_login_page(url: str, html: str) -> bool:
        """判断是否被踢回登录页。只看最终地址与正文提示，不匹配页脚退出链接。"""
        if "member.php?mod=logging" in str(url or ""):
            return True
        return parse_checkin_state(html) == "login"

    def checkin(self, raw_cookie: str) -> CheckinResult:
        """执行签到并采集积分。已签到不会重复提交。"""
        self._opener = self._build_opener(raw_cookie)
        try:
            _status, page, url = self._request(CHECKIN_PATH)
            if self._is_login_page(url, page):
                raise CookieExpiredError("论坛登录已失效，请更新 Cookie")
            state = parse_checkin_state(page)
            if state == "unknown":
                raise CheckinError("未识别到签到入口，论坛页面结构可能已变化")

            status, message = "already", "今日已签到"
            if state == "ready":
                formhash = parse_formhash(page)
                if not formhash:
                    raise CheckinError("签到页缺少 formhash，无法提交")
                code, payload, _purl = self._request(
                    CHECKIN_SIGN_PATH,
                    data={"checkinsubmit": "yes", "formhash": formhash},
                    ajax=True,
                )
                if code >= 400:
                    raise CheckinError(f"签到接口返回 HTTP {code}")
                status, message = self._judge_sign_response(payload)
                _code, page, url = self._request(CHECKIN_PATH)
                if self._is_login_page(url, page):
                    raise CookieExpiredError("签到过程中登录失效，请更新 Cookie")
                if parse_checkin_state(page) != "already" and status == "success":
                    status, message = "failed", "提交后未确认签到成功，请稍后重试"

            stats = self._collect_stats(page)
            return CheckinResult(status, message, stats)
        except (CookieExpiredError, CheckinError):
            raise
        except Exception as err:  # noqa: BLE001 - 统一收敛成业务异常
            raise CheckinError(f"签到请求失败：{err}") from err

    @staticmethod
    def _judge_sign_response(payload: str) -> Tuple[str, str]:
        """解析签到接口的 JSON 返回。"""
        text = str(payload or "")
        message = ""
        matched = re.search(r'"message"\s*:\s*"([^"]*)"', text)
        if matched:
            message = matched.group(1)
        if '"success":true' in text.replace(" ", ""):
            return "success", message or "签到成功"
        if "已签到" in message:
            return "already", message
        return "failed", message or "签到接口未返回成功标识"

    def _collect_stats(self, checkin_page: str) -> CheckinStats:
        """读取连续天数、余额、今日积分明细与官方规则；任何一步失败都降级。"""
        streak = None
        try:
            streak = parse_streak(checkin_page)
        except Exception:  # noqa: BLE001
            pass

        balance = today_total = None
        actions: Tuple[ActionStat, ...] = ()
        cap: Optional[int] = None
        try:
            _code, credit_page, credit_url = self._request(CREDIT_PATH)
            if self._is_login_page(credit_url, credit_page):
                raise CookieExpiredError("论坛登录已失效，请更新 Cookie")
            balance = parse_balance(credit_page)
            rows = parse_credit_rows(credit_page)
            _rcode, rule_page, _rurl = self._request(RULE_PATH)
            rules = parse_rules(rule_page)
            if rules:
                actions = build_action_stats(rules, rows)
                cap = daily_cap(rules)
            today = datetime.now().strftime("%Y-%m-%d")
            gains = [
                _to_int(row.get("delta")) or 0
                for row in rows if str(row.get("time", "")).startswith(today)
            ]
            today_total = sum(gains) if gains else 0
        except CookieExpiredError:
            raise
        except Exception:  # noqa: BLE001 - 积分读不到不影响签到结论
            pass

        today_gain = None
        for item in actions:
            if "签到" in item.name and item.count:
                today_gain = item.points
                break

        return CheckinStats(
            balance=balance,
            today_gain=today_gain,
            today_total=today_total,
            streak=streak,
            actions=actions,
            daily_cap=cap,
        )


def _clean(cell: str) -> str:
    """单元格去标签、压空白。"""
    text = _TAG_RE.sub(" ", str(cell or ""))
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _to_int(value: Any) -> Optional[int]:
    """把带千分位或正负号的数字文本转成整数。"""
    try:
        return int(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None
