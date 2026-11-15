"""MoviePilot V3 绿联论坛签到插件。"""

from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.triggers.cron import CronTrigger

from app.schemas.types import NotificationType
from app.sdk import scheduler as scheduler_sdk
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase

from .client import CheckinError, CookieExpiredError, UgreenCheckinClient


class UgreenSignIn(_PluginBase):
    """定时完成绿联社区每日签到，并保存可检查的执行结果。"""

    plugin_name = "绿联论坛签到"
    plugin_desc = "通过论坛真实签到页自动打卡，支持定时执行、结果复核、通知和历史记录。"
    plugin_icon = "https://raw.githubusercontent.com/silence9527/MoviePilot-UgreenSignIn/main/icons/ugreen.svg"
    plugin_version = "1.0.0"
    plugin_author = "silence9527"
    author_url = "https://github.com/silence9527"
    plugin_config_prefix = "ugreensignin_"
    plugin_order = 50
    auth_level = 1

    _enabled = False
    _notify = True
    _cookie = ""
    _cron = "0 8 * * *"
    _timeout = 90
    _history_limit = 90

    def __init__(self) -> None:
        super().__init__()
        self._sign_lock = Lock()
        self._config: Dict[str, Any] = {}

    def init_plugin(self, config: Optional[Dict[str, Any]] = None) -> None:
        """读取配置。重复初始化时先取消旧的立即任务。"""
        self.stop_service()
        current = dict(config or {})
        self._enabled = bool(current.get("enabled", False))
        self._notify = bool(current.get("notify", True))
        self._cookie = str(current.get("cookie") or "").strip()
        self._cron = str(current.get("cron") or "0 8 * * *").strip()
        self._timeout = self._bounded_int(current.get("timeout"), 90, 30, 180)
        self._config = current

        if self._enabled:
            try:
                CronTrigger.from_crontab(self._cron)
                logger.info(f"绿联论坛签到定时服务已配置：{self._cron}")
            except (TypeError, ValueError) as err:
                logger.error(f"绿联论坛签到 Cron 表达式无效：{self._cron}，{err}")

        if current.get("onlyonce"):
            current["onlyonce"] = False
            self._config = current
            queued = scheduler_sdk.add_plugin_once_job(
                plugin_id=self.__class__.__name__,
                job_id="UgreenSignInOnce",
                func=self.sign,
                name="绿联论坛立即签到",
                delay_seconds=3,
            )
            if not queued:
                logger.error("绿联论坛签到：立即任务未能加入宿主调度器")
            self.update_config(current)

    def get_state(self) -> bool:
        """返回插件是否启用。"""
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """本插件不注册远程命令。"""
        return []

    @staticmethod
    def get_api() -> List[Dict[str, Any]]:
        """本插件不注册额外 API。"""
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        """注册宿主管理的周期签到服务。"""
        if not self._enabled or not self._cron:
            return []
        try:
            trigger = CronTrigger.from_crontab(self._cron)
        except (TypeError, ValueError):
            return []
        return [{
            "id": "UgreenSignInDaily",
            "name": "绿联论坛每日签到",
            "trigger": trigger,
            "func": self.sign,
            "kwargs": {},
        }]

    def sign(self) -> Dict[str, Any]:
        """执行一次签到。并发触发时跳过重入，避免重复点击。"""
        if not self._sign_lock.acquire(blocking=False):
            logger.warning("绿联论坛签到：上一次任务仍在运行，本次跳过")
            return self._result("skipped", "上一次签到尚未结束")

        try:
            logger.info("绿联论坛签到：开始执行")
            if not self._cookie:
                raise CookieExpiredError("请先配置论坛 Cookie")
            result = UgreenCheckinClient(timeout=self._timeout).checkin(self._cookie)
            record = self._result(result.status, result.message)
        except CookieExpiredError as err:
            record = self._result("failed", str(err))
        except CheckinError as err:
            record = self._result("failed", str(err))
        except Exception as err:
            logger.error(f"绿联论坛签到：未处理异常 {err}", exc_info=True)
            record = self._result("failed", f"签到异常：{err}")
        finally:
            self._sign_lock.release()

        self._save_record(record)
        log_method = logger.error if record["status"] == "failed" else logger.info
        log_method(f"绿联论坛签到：{record['message']}")
        if self._notify and record["status"] != "skipped":
            title = "绿联论坛签到失败" if record["status"] == "failed" else "绿联论坛签到"
            text = f"结果：{record['message']}\n时间：{record['time']}"
            try:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title=title,
                    text=text,
                )
            except Exception as err:
                logger.error(f"绿联论坛签到：通知发送失败 {err}")
        return record

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回插件配置表单。"""
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            self._col("VSwitch", "enabled", "启用插件", md=4),
                            self._col("VSwitch", "notify", "发送通知", md=4),
                            self._col("VSwitch", "onlyonce", "立即运行一次", md=4),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [{
                                "component": "VTextarea",
                                "props": {
                                    "model": "cookie",
                                    "label": "论坛 Cookie",
                                    "rows": 4,
                                    "placeholder": "粘贴完整 Cookie，必须包含 *_auth",
                                    "hint": "Cookie 仅保存在 MoviePilot 插件配置中，不会上传至插件仓库。",
                                    "persistentHint": True,
                                },
                            }],
                        }],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            self._col("VCronField", "cron", "签到周期", md=6),
                            self._col("VTextField", "timeout", "浏览器超时秒数", md=6,
                                      extra={"type": "number", "min": 30, "max": 180}),
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [{
                                "component": "VAlert",
                                "props": {
                                    "type": "info",
                                    "variant": "tonal",
                                    "text": "使用论坛 ugreen_checkin 独立签到页。插件点击签到后会刷新页面复核；"
                                            "今日已签到不会重复提交。Cookie 失效时请重新登录论坛并更新。",
                                },
                            }],
                        }],
                    },
                ],
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "cookie": "",
            "cron": "0 8 * * *",
            "timeout": 90,
        }

    def get_page(self) -> List[dict]:
        """展示最近结果与历史，不展示 Cookie 等敏感配置。"""
        latest = self.get_data("last_result") or {}
        if not isinstance(latest, dict):
            latest = {}
        history = self.get_data("history") or []
        if not isinstance(history, list):
            history = []
        history = sorted(
            [item for item in history if isinstance(item, dict)],
            key=lambda item: str(item.get("time") or ""),
            reverse=True,
        )
        if not latest and not history:
            return [{
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": "暂无签到记录。配置 Cookie 后，可开启立即运行一次进行验证。",
                },
            }]

        status = str(latest.get("status") or "unknown")
        alert_type = {
            "success": "success",
            "already": "info",
            "skipped": "warning",
            "failed": "error",
        }.get(status, "warning")
        content: List[dict] = [{
            "component": "VAlert",
            "props": {
                "type": alert_type,
                "variant": "tonal",
                "title": str(latest.get("message") or "暂无结果"),
                "text": f"最近执行时间：{latest.get('time') or '未知'}",
                "class": "mb-4",
            },
        }]

        rows = []
        for item in history[:30]:
            item_status = str(item.get("status") or "unknown")
            color = {
                "success": "success",
                "already": "info",
                "skipped": "warning",
                "failed": "error",
            }.get(item_status, "grey")
            rows.append({
                "component": "tr",
                "content": [
                    {"component": "td", "text": str(item.get("time") or "-")},
                    {
                        "component": "td",
                        "content": [{
                            "component": "VChip",
                            "props": {"size": "small", "variant": "tonal", "color": color},
                            "text": self._status_label(item_status),
                        }],
                    },
                    {"component": "td", "text": str(item.get("message") or "-")},
                ],
            })

        if rows:
            content.append({
                "component": "VCard",
                "props": {"variant": "outlined"},
                "content": [
                    {"component": "VCardTitle", "text": "最近签到记录"},
                    {
                        "component": "VTable",
                        "props": {"density": "comfortable", "hover": True},
                        "content": [
                            {
                                "component": "thead",
                                "content": [{
                                    "component": "tr",
                                    "content": [
                                        {"component": "th", "text": "时间"},
                                        {"component": "th", "text": "状态"},
                                        {"component": "th", "text": "结果"},
                                    ],
                                }],
                            },
                            {"component": "tbody", "content": rows},
                        ],
                    },
                ],
            })
        return content

    def stop_service(self) -> None:
        """取消本插件的待执行一次任务；周期服务由宿主统一注销。"""
        try:
            scheduler_sdk.remove_plugin_once_job(
                self.__class__.__name__,
                "UgreenSignInOnce",
            )
        except Exception:
            pass

    def _save_record(self, record: Dict[str, Any]) -> None:
        """保存最近结果，并限制历史记录最多保留 90 条。"""
        self.save_data("last_result", record)
        history = self.get_data("history") or []
        if not isinstance(history, list):
            history = []
        history.append(record)
        self.save_data("history", history[-self._history_limit:])

    @staticmethod
    def _result(status: str, message: str) -> Dict[str, Any]:
        return {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "message": message,
        }

    @staticmethod
    def _status_label(status: str) -> str:
        return {
            "success": "签到成功",
            "already": "今日已签到",
            "failed": "失败",
            "skipped": "已跳过",
        }.get(status, "未知")

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    @staticmethod
    def _col(component: str, model: str, label: str, md: int,
             extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        props = {"model": model, "label": label}
        props.update(extra or {})
        return {
            "component": "VCol",
            "props": {"cols": 12, "md": md},
            "content": [{"component": component, "props": props}],
        }
