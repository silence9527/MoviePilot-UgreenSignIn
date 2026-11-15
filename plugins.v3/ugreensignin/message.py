"""通知文案渲染。

纯文本给所有渠道兜底，结构化段落交给宿主按渠道能力排版。
文案只表达签到事实，不引入额外配置。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

DIVIDER = "────────────"
_REWARD_STEP_FIRST = 7
_REWARD_STEP_SECOND = 30


def _row(icon: str, label: str, value: str) -> str:
    """两字标签用全角空格对齐，保证微信里等宽落位。"""
    return f"{icon} {label}　{value}"


def _gain_text(value: Optional[int], plus: str = "+") -> Optional[str]:
    """积分变化文案。结构化段落用全角加号，避开宿主的 Markdown 转义。"""
    if value is None:
        return None
    if value > 0:
        return f"{plus}{value} 积分"
    if value == 0:
        return "0 积分"
    return f"{value} 积分"


def _reward_hint(streak: Optional[int], plus: str = "+") -> str:
    """按连续天数给一句下一步奖励提示。"""
    if not streak or streak <= 0:
        return ""
    if streak < _REWARD_STEP_FIRST:
        return f"再签 {_REWARD_STEP_FIRST - streak} 天可得 {plus}5 积分"
    if streak < _REWARD_STEP_SECOND:
        return f"再签 {_REWARD_STEP_SECOND - streak} 天可得 {plus}20 积分"
    return "已连续满 30 天，奖励到手"


def build_text(record: Dict[str, Any]) -> str:
    """生成纯文本通知正文，企微等只收文本的渠道直接使用。

    标题已承担品牌与积分摘要，正文不再重复，只排成两栏卡片。
    """
    status = str(record.get("status") or "unknown")
    lines: List[str] = [DIVIDER]

    if status == "failed":
        lines.append(_row("❌", "状态", "签到失败"))
        reason = str(record.get("message") or "原因未知").strip()
        lines.append(_row("💬", "原因", reason))
    else:
        lines.append(_row("✅", "状态", str(record.get("message") or "已处理")))
        gain = _gain_text(record.get("today_gain"))
        if gain:
            lines.append(_row("💰", "本次", gain))
        balance = record.get("balance")
        if isinstance(balance, int):
            lines.append(_row("🏅", "余额", f"{balance} 积分"))
        streak = record.get("streak")
        if isinstance(streak, int):
            lines.append(_row("🔥", "连续", f"{streak} 天"))

    lines.append(_row("🕘", "时间", _short_time(record.get("time"))))
    lines.append(DIVIDER)

    if status == "failed":
        lines.append("更新 Cookie 后可点「立即运行一次」重试")
    else:
        hint = _reward_hint(record.get("streak"))
        if hint:
            lines.append(hint)

    return "\n".join(lines)


def build_rich_content(record: Dict[str, Any]) -> Optional[Any]:
    """生成渠道无关的结构化通知，供支持 Markdown 的渠道排版。

    宿主不可用时返回 None，调用方继续发纯文本。
    """
    try:
        from app.schemas.message import NotificationBlock, NotificationContent
    except Exception:
        return None

    status = str(record.get("status") or "unknown")
    blocks: List[Any] = []
    if status == "failed":
        title = "绿联社区签到失败"
        blocks.append(NotificationBlock(title="状态", text="签到失败"))
        blocks.append(NotificationBlock(
            title="原因", text=str(record.get("message") or "原因未知").strip()))
    else:
        title = "绿联社区签到成功"
        blocks.append(NotificationBlock(
            title="状态", text=str(record.get("message") or "已处理")))
        gain = _gain_text(record.get("today_gain"), plus="＋")
        if gain:
            blocks.append(NotificationBlock(title="本次获得", text=gain))
        balance = record.get("balance")
        if isinstance(balance, int):
            blocks.append(NotificationBlock(title="积分余额", text=f"{balance} 积分"))
        streak = record.get("streak")
        if isinstance(streak, int):
            blocks.append(NotificationBlock(title="连续签到", text=f"{streak} 天"))

    blocks.append(NotificationBlock(title="完成时间", text=_friendly_time(record.get("time"))))
    hint = _reward_hint(record.get("streak"), plus="＋")
    if hint and status != "failed":
        blocks.append(NotificationBlock(text=hint))

    return NotificationContent(title=title, blocks=blocks)


def build_notification(record: Dict[str, Any]) -> Tuple[str, str, Optional[Any]]:
    """一次返回通知标题、纯文本和结构化内容。"""
    status = str(record.get("status") or "unknown")
    if status == "failed":
        title = "绿联社区签到失败"
    else:
        title = "绿联社区签到"
        gain = record.get("today_gain")
        balance = record.get("balance")
        if isinstance(gain, int):
            title = f"{title} · +{gain} 积分"
        if isinstance(balance, int):
            title = f"{title} · 余额 {balance}"
    return title, build_text(record), build_rich_content(record)


def _short_time(value: Any) -> str:
    """把 2026-11-16 08:00:12 压成 11-16 08:00。"""
    text = str(value or "").strip()
    parts = text.split(" ")
    if len(parts) == 2 and len(parts[0]) == 10:
        return f"{parts[0][5:]} {parts[1][:5]}"
    return text or "未知"


def _friendly_time(value: Any) -> str:
    """把 2026-11-16 08:00:12 写成 11月16日 08:00。

    结构化段落会被宿主转义，这里刻意避开会被转义的符号。
    """
    text = str(value or "").strip()
    parts = text.split(" ")
    if len(parts) == 2 and len(parts[0]) == 10:
        month_day = parts[0][5:].split("-")
        if len(month_day) == 2:
            return f"{int(month_day[0])}月{int(month_day[1])}日 {parts[1][:5]}"
    return text or "未知"
