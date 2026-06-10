from __future__ import annotations

import json
import re
import asyncio
import copy
import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

from quart import request
from sqlmodel import col, func, select

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
try:
    from astrbot.api.star import Context, Star, StarTools
except ImportError:  # pragma: no cover - kept for older AstrBot builds.
    from astrbot.api.star import Context, Star

    StarTools = None  # type: ignore[assignment]
from astrbot.core.db.po import ProviderStat
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.message_type import MessageType

try:
    from .backend.history_stats import HistoryStatsMixin
    from .backend.user_limits import UserLimitMixin
    from .backend.user_stats import UserStatsMixin
except ImportError:  # pragma: no cover - compatible with direct module loading.
    from backend.history_stats import HistoryStatsMixin
    from backend.user_limits import UserLimitMixin
    from backend.user_stats import UserStatsMixin


PLUGIN_NAME = "astrbot_plugin_token_controller"
LEGACY_PLUGIN_NAME = "astrbot_plugin_token_limit"
GROUP_REMARKS_FILE = "group_remarks.json"
GROUP_LIMITS_FILE = "group_limits.json"
PLUGIN_DATA_FILES = (
    GROUP_REMARKS_FILE,
    GROUP_LIMITS_FILE,
    "history_usage.json",
    "user_usage_48h.json",
)
MAX_GROUP_REMARK_LENGTH = 64
GROUP_SETTING_DAILY_LIMIT = "daily_token_limit"
GROUP_SETTING_ONLY_AT_BOT = "only_at_bot_llm"
GROUP_SETTING_CONTEXT_LIMIT_05 = "context_limit_05"
GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY = "prompt_cache_key_strategy"
GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY = "deepseek_cache_strategy"
GROUP_SETTINGS_CONFIG_BACKUP_KEY = "_plugin_page_group_settings"
TOKEN_LIMIT_CONTEXT_RATIO = 0.005
TOKEN_LIMIT_CONTEXT_TRIM_RATIO = 0.8
TOKEN_LIMIT_CONTEXT_COMPRESS_THRESHOLD = 0.82
TOKEN_LIMIT_CONTEXT_FALLBACK_TURNS = 3
TOKEN_LIMIT_TEMP_PROVIDER_PREFIX = "__token_limit_context__"
TOKEN_LIMIT_OPENAI_CACHE_RETENTION_MODELS = ("gpt-5", "gpt-4.1")
TOKEN_LIMIT_PROMPT_CACHE_ANCHOR_VERSION = "v1"
TOKEN_LIMIT_PROMPT_CACHE_ANCHOR_LINES = 128
TOKEN_LIMIT_PROMPT_CACHE_PREFIX_TARGET = 1400
TOKEN_LIMIT_IMAGE_TOKEN_ESTIMATE = 765
TOKEN_LIMIT_AUDIO_TOKEN_ESTIMATE = 500
OVER_LIMIT_STOP = "stop_llm"
OVER_LIMIT_FALLBACK = "fallback_provider"
TOKEN_FIELDS_SUM = (
    ProviderStat.token_input_other
    + ProviderStat.token_input_cached
    + ProviderStat.token_output
)


CONFIG_SCHEMA: dict[str, dict[str, Any]] = {
    "enabled": {
        "description": "启用插件",
        "type": "bool",
        "hint": "关闭后不统计限流状态，也不会拦截任何 LLM 请求。",
        "default": True,
    },
    "limited_groups": {
        "description": "需要限流的 QQ 群聊列表",
        "type": "list",
        "hint": "填写 QQ 群号。原生 WebUI 可逐项填写；也兼容逗号、空格或换行分隔的字符串。新加入的群号会从加入时刻开始写入历史 token 用量统计；之后即使从列表移除也会继续统计。",
        "default": [],
    },
    "daily_token_limit": {
        "description": "单个群聊每日用量上限",
        "type": "int",
        "hint": "单位为 token。当前统计窗口内群聊总用量到达上限后，根据“用量超限时的措施”停止调用 LLM 或切换到回退模型。",
        "default": 1000000,
    },
    "user_daily_token_limit": {
        "description": "单个用户每日用量上限",
        "type": "int",
        "hint": "单位为 token。仅统计单个群聊内单个用户当前刷新周期的用量；设置为 -1 时不限制单个用户用量。超过上限后静默阻断该用户在该群内发起的 LLM 请求。",
        "default": -1,
    },
    "over_limit_policy": {
        "description": "用量超限时的措施",
        "type": "object",
        "hint": "配置群聊达到每日用量上限后的处理方式。",
        "default": {
            "action": OVER_LIMIT_STOP,
            "fallback_provider_id": "",
            "fallback_token_limit": 0,
            "block_wake_words_after_limit": False,
        },
        "items": {
            "action": {
                "description": "处理方式",
                "type": "string",
                "hint": "选择“停止调用 LLM”时，原始模型用量超限后直接拦截；选择“回退到其他模型”时，会改用回退供应商继续生成回复。",
                "default": OVER_LIMIT_STOP,
                "options": [OVER_LIMIT_STOP, OVER_LIMIT_FALLBACK],
                "option_labels": ["停止调用 LLM", "回退到其他模型"],
            },
            "fallback_provider_id": {
                "description": "回退的模型供应商",
                "type": "string",
                "hint": "填写 AstrBot 模型供应商 ID。Plugin Page 会提供当前已加载的聊天模型供应商下拉选择。",
                "default": "",
            },
            "fallback_token_limit": {
                "description": "回退模型的用量上限",
                "type": "int",
                "hint": "单位为 token。选择回退时，硬上限为“每日用量上限 + 回退模型的用量上限”；当前统计窗口内群聊总用量到达硬上限后停止调用。",
                "default": 0,
            },
            "block_wake_words_after_limit": {
                "description": "超限后不再响应唤醒词",
                "type": "bool",
                "hint": "启用后，群聊当前窗口用量达到“单个群聊每日用量上限”时，使用唤醒词触发 bot 的事件会被阻断；@bot 仍可继续触发，但仍遵守回退模型或停止响应规则。",
                "default": False,
            },
        },
    },
    "refresh_time": {
        "description": "用量刷新时间",
        "type": "string",
        "hint": "每天按本机时区在该时间切换统计窗口，格式 HH:MM，例如 00:00 或 04:30。",
        "default": "00:00",
    },
    "qq_platform_names": {
        "description": "QQ 平台适配器名称",
        "type": "list",
        "hint": "只有这些平台类型的群聊会被限流。默认覆盖 aiocqhttp、qq_official 和 qq_official_webhook。",
        "default": ["aiocqhttp", "qq_official", "qq_official_webhook"],
    },
    "match_unique_session": {
        "description": "兼容会话隔离统计",
        "type": "bool",
        "hint": "开启后统计当前窗口和历史用量时，会匹配常见 unique_session 形式，例如 用户ID_群号。",
        "default": True,
    },
    "block_message": {
        "description": "超限拦截提示",
        "type": "text",
        "hint": "达到停止调用条件时发送到群聊。可用变量：{group_id}、{used}、{limit}、{refresh_time}、{window_start}、{window_end}。",
        "default": "本群今日 LLM token 用量已达上限（{used}/{limit}），将在 {refresh_time} 后恢复。",
    },
    "send_block_message": {
        "description": "发送拦截提示",
        "type": "bool",
        "hint": "关闭后只拦截 LLM 请求，不向群内发送提示消息。",
        "default": True,
    },
}


@dataclass(frozen=True)
class UsageWindow:
    start_local: datetime
    end_local: datetime
    start_utc: datetime
    end_utc: datetime


def _ok(data: dict | list | None = None, message: str | None = None) -> dict:
    return {"status": "ok", "message": message, "data": data or {}}


def _error(message: str) -> dict:
    return {"status": "error", "message": message, "data": {}}


def _split_group_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [str(int(value))]
    if isinstance(value, str):
        return [item for item in re.split(r"[\s,;，；]+", value.strip()) if item]
    if isinstance(value, list):
        groups: list[str] = []
        for item in value:
            groups.extend(_split_group_values(item))
        return groups
    return []


def _normalize_group_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def _format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} M"
    if value >= 1_000:
        return f"{value / 1_000:.2f} K"
    return str(value)


def _format_context_limit_tokens(value: int) -> str:
    value = max(1, int(value or 0))
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    return f"{max(1, round(value / 1_000))}K"


def _prompt_cache_anchor_text() -> str:
    lines = [
        "<token_limit_prompt_cache_anchor>",
        f"version={TOKEN_LIMIT_PROMPT_CACHE_ANCHOR_VERSION}",
        "purpose=stable_prefix_for_prompt_cache",
    ]
    for index in range(TOKEN_LIMIT_PROMPT_CACHE_ANCHOR_LINES):
        lines.append(f"anchor_line_{index:03d}=stable_cache_prefix_no_instruction")
    lines.append("</token_limit_prompt_cache_anchor>")
    return "\n".join(lines)


TOKEN_LIMIT_PROMPT_CACHE_ANCHOR = _prompt_cache_anchor_text()


def _parse_refresh_time(value: Any) -> time:
    raw = str(value or "00:00").strip()
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", raw)
    if not match:
        return time(hour=0, minute=0)
    return time(hour=int(match.group(1)), minute=int(match.group(2)))


def _local_timezone() -> tzinfo:
    tz = datetime.now().astimezone().tzinfo
    return tz or timezone.utc


def _build_usage_window(refresh_time: Any) -> UsageWindow:
    local_tz = _local_timezone()
    now_local = datetime.now(local_tz)
    parsed_time = _parse_refresh_time(refresh_time)
    today_refresh = datetime.combine(now_local.date(), parsed_time, tzinfo=local_tz)
    if now_local >= today_refresh:
        start_local = today_refresh
    else:
        start_local = today_refresh - timedelta(days=1)
    end_local = start_local + timedelta(days=1)
    return UsageWindow(
        start_local=start_local,
        end_local=end_local,
        start_utc=start_local.astimezone(timezone.utc),
        end_utc=end_local.astimezone(timezone.utc),
    )


class Main(UserLimitMixin, UserStatsMixin, HistoryStatsMixin, Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = config if config is not None else {}
        self.group_remarks_path = self._resolve_group_remarks_path()
        self.group_limits_path = self._resolve_group_limits_path()
        self.history_stats_path = self._resolve_history_stats_path()
        self.user_stats_path = self._resolve_user_stats_path()
        self._history_last_sync_attempt: datetime | None = None
        self._history_sync_lock = asyncio.Lock()
        self._user_last_sync_attempts: dict[str, datetime] = {}
        self._user_sync_lock = asyncio.Lock()
        self._ensure_history_tracking_for_current_groups()
        self._ensure_user_tracking_for_current_groups()
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/config",
            self.api_get_config,
            ["GET"],
            "获取 QQ 群 token 限流插件配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/config",
            self.api_save_config,
            ["POST"],
            "保存 QQ 群 token 限流插件配置",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/usage",
            self.api_get_usage,
            ["GET"],
            "获取 QQ 群 token 用量统计",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/history",
            self.api_get_history,
            ["GET"],
            "获取 QQ 群 token 历史用量统计",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/user-usage",
            self.api_get_user_usage,
            ["GET"],
            "获取 QQ 群今日用户 token 用量统计",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/providers",
            self.api_get_providers,
            ["GET"],
            "获取可用于回退的 LLM 模型供应商列表",
        )

        self.context.register_web_api(
            f"/{PLUGIN_NAME}/remarks",
            self.api_get_remarks,
            ["GET"],
            "Get QQ group remarks",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/remarks",
            self.api_save_remark,
            ["POST"],
            "Save QQ group remark",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/group-settings",
            self.api_get_group_settings,
            ["GET"],
            "Get QQ group personalized settings",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/group-settings",
            self.api_save_group_settings,
            ["POST"],
            "Save QQ group personalized settings",
        )

    async def initialize(self) -> None:
        self._start_history_background_sync()
        self._start_user_background_sync()

    async def terminate(self) -> None:
        await self._stop_history_background_sync()
        await self._stop_user_background_sync()

    def _resolve_group_remarks_path(self) -> Path:
        if StarTools is not None:
            try:
                data_dir = StarTools.get_data_dir(PLUGIN_NAME)
                self._copy_legacy_data_files(data_dir)
                return data_dir / GROUP_REMARKS_FILE
            except Exception as exc:
                logger.warning("Failed to get plugin data dir; using plugin dir: %s", exc)
        return Path(__file__).resolve().with_name(GROUP_REMARKS_FILE)

    def _copy_legacy_data_files(self, data_dir: Path) -> None:
        if LEGACY_PLUGIN_NAME == PLUGIN_NAME:
            return
        legacy_data_dir = data_dir.with_name(LEGACY_PLUGIN_NAME)
        if legacy_data_dir == data_dir or not legacy_data_dir.exists():
            return
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning("Failed to prepare plugin data dir for migration: %s", exc)
            return
        for file_name in PLUGIN_DATA_FILES:
            source = legacy_data_dir / file_name
            target = data_dir / file_name
            if target.exists() or not source.is_file():
                continue
            try:
                target.write_bytes(source.read_bytes())
            except Exception as exc:
                logger.warning(
                    "Failed to copy legacy plugin data file %s: %s",
                    file_name,
                    exc,
                )

    def _resolve_group_limits_path(self) -> Path:
        remarks_path = getattr(self, "group_remarks_path", None)
        if isinstance(remarks_path, Path):
            return remarks_path.with_name(GROUP_LIMITS_FILE)
        return Path(__file__).resolve().with_name(GROUP_LIMITS_FILE)

    def _load_group_remarks(self) -> dict[str, str]:
        if not self.group_remarks_path.exists():
            return {}
        try:
            raw_data = json.loads(self.group_remarks_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read QQ group remarks: %s", exc)
            return {}
        if not isinstance(raw_data, dict):
            return {}

        remarks: dict[str, str] = {}
        for group_id, remark in raw_data.items():
            normalized_group_id = _normalize_group_id(group_id)
            normalized_remark = self._sanitize_group_remark(remark)
            if normalized_group_id and normalized_remark:
                remarks[normalized_group_id] = normalized_remark
        return remarks

    def _save_group_remarks(self, remarks: dict[str, str]) -> None:
        try:
            self.group_remarks_path.parent.mkdir(parents=True, exist_ok=True)
            self.group_remarks_path.write_text(
                json.dumps(remarks, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            raise ValueError(f"保存 QQ 群备注失败: {exc}") from exc

    @staticmethod
    def _sanitize_group_remark(value: Any) -> str:
        return str(value or "").strip()[:MAX_GROUP_REMARK_LENGTH]

    def _normalize_group_settings_data(
        self,
        raw_data: Any,
    ) -> dict[str, dict[str, Any]]:
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except (TypeError, ValueError):
                return {}
        if not isinstance(raw_data, dict):
            return {}

        settings: dict[str, dict[str, Any]] = {}
        for group_id, value in raw_data.items():
            normalized_group_id = _normalize_group_id(group_id)
            if not normalized_group_id:
                continue
            if isinstance(value, dict):
                item: dict[str, Any] = {}
                if GROUP_SETTING_DAILY_LIMIT in value:
                    try:
                        item[GROUP_SETTING_DAILY_LIMIT] = max(
                            0,
                            int(value.get(GROUP_SETTING_DAILY_LIMIT) or 0),
                        )
                    except (TypeError, ValueError):
                        pass
                if bool(value.get(GROUP_SETTING_ONLY_AT_BOT, False)):
                    item[GROUP_SETTING_ONLY_AT_BOT] = True
                if bool(value.get(GROUP_SETTING_CONTEXT_LIMIT_05, False)):
                    item[GROUP_SETTING_CONTEXT_LIMIT_05] = True
                if bool(value.get(GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY, False)):
                    item[GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY] = True
                if bool(value.get(GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY, False)):
                    item[GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY] = True
                if item:
                    settings[normalized_group_id] = item
                continue
            try:
                settings[normalized_group_id] = {
                    GROUP_SETTING_DAILY_LIMIT: max(0, int(value or 0))
                }
            except (TypeError, ValueError):
                continue
        return settings

    @staticmethod
    def _merge_group_settings(
        base: dict[str, dict[str, Any]],
        extra: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        merged = {group_id: dict(settings) for group_id, settings in base.items()}
        for group_id, settings in extra.items():
            item = dict(merged.get(group_id, {}))
            item.update(settings)
            if item:
                merged[group_id] = item
        return merged

    def _read_group_settings_file(self, path: Path) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "Failed to read QQ group personalized settings from %s: %s",
                path,
                exc,
            )
            return {}
        return self._normalize_group_settings_data(raw_data)

    def _write_group_settings_file(
        self,
        settings: dict[str, dict[str, Any]],
    ) -> None:
        normalized_settings = self._normalize_group_settings_data(settings)
        self.group_limits_path.parent.mkdir(parents=True, exist_ok=True)
        self.group_limits_path.write_text(
            json.dumps(normalized_settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_group_settings_config_backup(self) -> dict[str, dict[str, Any]]:
        try:
            raw_data = (
                self.config.get(GROUP_SETTINGS_CONFIG_BACKUP_KEY)
                if hasattr(self.config, "get")
                else (
                    self.config[GROUP_SETTINGS_CONFIG_BACKUP_KEY]
                    if GROUP_SETTINGS_CONFIG_BACKUP_KEY in self.config
                    else None
                )
            )
        except Exception:
            return {}
        return self._normalize_group_settings_data(raw_data)

    def _set_group_settings_config_backup(
        self,
        settings: dict[str, dict[str, Any]],
        *,
        save_config: bool = True,
    ) -> bool:
        normalized_settings = self._normalize_group_settings_data(settings)
        try:
            current_settings = self._load_group_settings_config_backup()
            changed = current_settings != normalized_settings
            if changed:
                if normalized_settings:
                    self.config[GROUP_SETTINGS_CONFIG_BACKUP_KEY] = normalized_settings
                else:
                    self.config.pop(GROUP_SETTINGS_CONFIG_BACKUP_KEY, None)
            if changed and save_config:
                config_save = getattr(self.config, "save_config", None)
                if callable(config_save):
                    config_save()
            return True
        except Exception as exc:
            logger.warning("Failed to save group settings config backup: %s", exc)
            return False

    def _group_settings_fallback_paths(self) -> list[Path]:
        paths: list[Path] = []
        legacy_path = (
            self.group_limits_path.parent.with_name(LEGACY_PLUGIN_NAME)
            / GROUP_LIMITS_FILE
        )
        plugin_dir_path = Path(__file__).resolve().with_name(GROUP_LIMITS_FILE)
        for path in (legacy_path, plugin_dir_path):
            if path != self.group_limits_path and path not in paths:
                paths.append(path)
        return paths

    def _load_group_settings(self) -> dict[str, dict[str, Any]]:
        primary_exists = self.group_limits_path.exists()
        config_backup_settings = self._load_group_settings_config_backup()
        settings: dict[str, dict[str, Any]] = {}
        settings = self._merge_group_settings(settings, config_backup_settings)
        primary_settings = self._read_group_settings_file(self.group_limits_path)
        settings = self._merge_group_settings(settings, primary_settings)

        if not primary_exists and not settings:
            for path in self._group_settings_fallback_paths():
                fallback_settings = self._read_group_settings_file(path)
                if fallback_settings:
                    settings = self._merge_group_settings(settings, fallback_settings)

        if settings:
            if settings != primary_settings:
                try:
                    self._write_group_settings_file(settings)
                except Exception as exc:
                    logger.warning(
                        "Failed to backfill group settings file from backup: %s",
                        exc,
                    )
            self._set_group_settings_config_backup(settings)
        return settings

    def _save_group_settings(self, settings: dict[str, dict[str, Any]]) -> None:
        normalized_settings = self._normalize_group_settings_data(settings)
        file_error: Exception | None = None
        try:
            self._write_group_settings_file(normalized_settings)
        except Exception as exc:
            file_error = exc

        backup_saved = self._set_group_settings_config_backup(normalized_settings)
        if file_error is not None:
            if backup_saved:
                logger.warning(
                    "Failed to save group settings file; config backup was updated: %s",
                    file_error,
                )
                return
            raise ValueError(f"保存 QQ 群个性化配置失败: {file_error}") from file_error

    def _load_group_limits(self) -> dict[str, int]:
        limits: dict[str, int] = {}
        for group_id, settings in self._load_group_settings().items():
            if GROUP_SETTING_DAILY_LIMIT in settings:
                limits[group_id] = max(0, int(settings[GROUP_SETTING_DAILY_LIMIT] or 0))
        return limits

    def _save_group_limits(self, limits: dict[str, int]) -> None:
        current_settings = self._load_group_settings()
        next_settings: dict[str, dict[str, Any]] = {}
        for group_id, settings in current_settings.items():
            item: dict[str, Any] = {}
            if bool(settings.get(GROUP_SETTING_ONLY_AT_BOT, False)):
                item[GROUP_SETTING_ONLY_AT_BOT] = True
            if bool(settings.get(GROUP_SETTING_CONTEXT_LIMIT_05, False)):
                item[GROUP_SETTING_CONTEXT_LIMIT_05] = True
            if bool(settings.get(GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY, False)):
                item[GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY] = True
            if bool(settings.get(GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY, False)):
                item[GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY] = True
            if item:
                next_settings[group_id] = item
        for group_id, limit in limits.items():
            normalized_group_id = _normalize_group_id(group_id)
            if not normalized_group_id:
                continue
            item = dict(next_settings.get(normalized_group_id, {}))
            item[GROUP_SETTING_DAILY_LIMIT] = max(0, int(limit or 0))
            next_settings[normalized_group_id] = item
        self._save_group_settings(next_settings)

    def _config_value(self, key: str) -> Any:
        if key in self.config:
            return self.config[key]
        return CONFIG_SCHEMA[key].get("default")

    def _limited_groups(self) -> list[str]:
        seen: set[str] = set()
        groups: list[str] = []
        for item in _split_group_values(self._config_value("limited_groups")):
            group_id = _normalize_group_id(item)
            if group_id and group_id not in seen:
                seen.add(group_id)
                groups.append(group_id)
        return groups

    def _qq_platform_names(self) -> set[str]:
        values = _split_group_values(self._config_value("qq_platform_names"))
        return {item.strip() for item in values if item.strip()}

    def _qq_platform_ids(self) -> set[str]:
        configured_names = self._qq_platform_names()
        platform_ids: set[str] = set()
        platform_manager = getattr(self.context, "platform_manager", None)
        platform_insts = getattr(platform_manager, "platform_insts", []) or []
        for platform in platform_insts:
            meta = platform.meta()
            meta_name = getattr(meta, "name", "")
            meta_id = getattr(meta, "id", "")
            if not configured_names or meta_name in configured_names:
                if meta_id:
                    platform_ids.add(str(meta_id))
                if meta_name:
                    platform_ids.add(str(meta_name))
        if not platform_ids:
            platform_ids.update(configured_names)
        return platform_ids

    def _daily_limit(self) -> int:
        try:
            return max(0, int(self._config_value("daily_token_limit") or 0))
        except (TypeError, ValueError):
            return 0

    def _daily_limit_for_group(
        self,
        group_id: str,
        group_limits: dict[str, int] | None = None,
    ) -> int:
        normalized_group_id = _normalize_group_id(group_id)
        limits = group_limits if group_limits is not None else self._load_group_limits()
        if normalized_group_id in limits:
            return max(0, int(limits[normalized_group_id] or 0))
        return self._daily_limit()

    def _group_only_at_bot_llm(
        self,
        group_id: str,
        group_settings: dict[str, dict[str, Any]] | None = None,
    ) -> bool:
        normalized_group_id = _normalize_group_id(group_id)
        settings = (
            group_settings
            if group_settings is not None
            else self._load_group_settings()
        )
        return bool(
            settings.get(normalized_group_id, {}).get(
                GROUP_SETTING_ONLY_AT_BOT,
                False,
            )
        )

    def _group_context_limit_05(
        self,
        group_id: str,
        group_settings: dict[str, dict[str, Any]] | None = None,
    ) -> bool:
        normalized_group_id = _normalize_group_id(group_id)
        settings = (
            group_settings
            if group_settings is not None
            else self._load_group_settings()
        )
        return bool(
            settings.get(normalized_group_id, {}).get(
                GROUP_SETTING_CONTEXT_LIMIT_05,
                False,
            )
        )

    def _group_prompt_cache_key_strategy(
        self,
        group_id: str,
        group_settings: dict[str, dict[str, Any]] | None = None,
    ) -> bool:
        normalized_group_id = _normalize_group_id(group_id)
        settings = (
            group_settings
            if group_settings is not None
            else self._load_group_settings()
        )
        return bool(
            settings.get(normalized_group_id, {}).get(
                GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY,
                False,
            )
        )

    def _group_deepseek_cache_strategy(
        self,
        group_id: str,
        group_settings: dict[str, dict[str, Any]] | None = None,
    ) -> bool:
        normalized_group_id = _normalize_group_id(group_id)
        settings = (
            group_settings
            if group_settings is not None
            else self._load_group_settings()
        )
        return bool(
            settings.get(normalized_group_id, {}).get(
                GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY,
                False,
            )
        )

    def _group_context_limit_tokens(
        self,
        group_id: str,
        group_settings: dict[str, dict[str, Any]] | None = None,
        group_limits: dict[str, int] | None = None,
    ) -> int:
        if not self._group_context_limit_05(group_id, group_settings):
            return 0
        limit = self._daily_limit_for_group(group_id, group_limits)
        return max(1, int(limit * TOKEN_LIMIT_CONTEXT_RATIO))

    def _over_limit_policy(self) -> dict[str, Any]:
        raw_policy = self._config_value("over_limit_policy")
        default_policy = CONFIG_SCHEMA["over_limit_policy"]["default"]
        policy = dict(default_policy)
        if isinstance(raw_policy, dict):
            policy.update(raw_policy)

        action = str(policy.get("action") or OVER_LIMIT_STOP).strip()
        if action not in {OVER_LIMIT_STOP, OVER_LIMIT_FALLBACK}:
            action = OVER_LIMIT_STOP

        try:
            fallback_token_limit = max(
                0,
                int(policy.get("fallback_token_limit") or 0),
            )
        except (TypeError, ValueError):
            fallback_token_limit = 0

        return {
            "action": action,
            "fallback_provider_id": str(policy.get("fallback_provider_id") or "").strip(),
            "fallback_token_limit": fallback_token_limit,
            "block_wake_words_after_limit": bool(
                policy.get("block_wake_words_after_limit", False)
            ),
        }

    def _fallback_provider_id(self) -> str:
        policy = self._over_limit_policy()
        if policy["action"] != OVER_LIMIT_FALLBACK:
            return ""
        return str(policy["fallback_provider_id"] or "").strip()

    def _fallback_token_limit(self) -> int:
        return int(self._over_limit_policy()["fallback_token_limit"])

    def _fallback_provider_exists(self, provider_id: str) -> bool:
        if not provider_id:
            return False
        get_provider_by_id = getattr(self.context, "get_provider_by_id", None)
        if not callable(get_provider_by_id):
            return False
        provider = get_provider_by_id(provider_id)
        return provider is not None and hasattr(provider, "text_chat")

    def _provider_id_for_context_limit(
        self,
        event: AstrMessageEvent,
        limit_context: dict[str, Any],
    ) -> str:
        limit_state = limit_context.get("limit_state", {})
        if (
            limit_state.get("status") == "fallback"
            and limit_context.get("fallback_provider_valid")
        ):
            return str(limit_context.get("fallback_provider_id") or "").strip()
        selected_provider_id = str(
            self._event_get_extra(event, "selected_provider") or ""
        ).strip()
        if (
            selected_provider_id
            and not selected_provider_id.startswith(TOKEN_LIMIT_TEMP_PROVIDER_PREFIX)
        ):
            return selected_provider_id
        try:
            provider = self.context.get_using_provider(
                umo=getattr(event, "unified_msg_origin", None),
            )
        except Exception:
            provider = None
        provider_config = getattr(provider, "provider_config", None)
        if isinstance(provider_config, dict):
            provider_id = str(provider_config.get("id") or "").strip()
            if provider_id:
                return provider_id
        try:
            provider_meta = provider.meta()
            return str(getattr(provider_meta, "id", "") or "").strip()
        except Exception:
            return ""

    def _cleanup_temp_context_provider(self, event: AstrMessageEvent) -> None:
        temp_provider_id = self._event_get_extra(
            event,
            "token_limit_temp_context_provider_id",
        )
        if not temp_provider_id:
            event.set_extra("token_limit_context_provider_origin", "")
            event.set_extra("token_limit_context_tokens", 0)
            event.set_extra("token_limit_prompt_cache_key", "")
            event.set_extra("token_limit_prompt_cache_kind", "")
            event.set_extra("token_limit_prompt_cache_active", False)
            event.set_extra("token_limit_context_provider_had_selected", False)
            return
        provider_manager = getattr(self.context, "provider_manager", None)
        inst_map = getattr(provider_manager, "inst_map", None)
        provider_insts = getattr(provider_manager, "provider_insts", None)
        if isinstance(inst_map, dict):
            temp_provider = inst_map.pop(str(temp_provider_id), None)
        else:
            temp_provider = None
        if isinstance(provider_insts, list) and temp_provider in provider_insts:
            provider_insts.remove(temp_provider)
        selected_provider_id = str(
            self._event_get_extra(event, "selected_provider") or ""
        )
        origin_provider_id = str(
            self._event_get_extra(event, "token_limit_context_provider_origin")
            or ""
        )
        had_selected_provider = bool(
            self._event_get_extra(event, "token_limit_context_provider_had_selected")
        )
        if selected_provider_id == str(temp_provider_id):
            event.set_extra(
                "selected_provider",
                origin_provider_id if had_selected_provider else "",
            )
        event.set_extra("token_limit_temp_context_provider_id", "")
        event.set_extra("token_limit_context_provider_origin", "")
        event.set_extra("token_limit_context_tokens", 0)
        event.set_extra("token_limit_prompt_cache_key", "")
        event.set_extra("token_limit_prompt_cache_kind", "")
        event.set_extra("token_limit_prompt_cache_active", False)
        event.set_extra("token_limit_context_provider_had_selected", False)

    def _set_temp_context_provider_limit(
        self,
        event: AstrMessageEvent,
        context_tokens: int,
    ) -> bool:
        temp_provider_id = str(
            self._event_get_extra(event, "token_limit_temp_context_provider_id") or ""
        )
        if not temp_provider_id:
            return False
        provider_manager = getattr(self.context, "provider_manager", None)
        inst_map = getattr(provider_manager, "inst_map", None)
        if not isinstance(inst_map, dict):
            return False
        provider = inst_map.get(temp_provider_id)
        if provider is None:
            return False
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return False

        effective_context = max(1, int(context_tokens))
        provider_config["max_context_tokens"] = effective_context
        try:
            provider.max_context_tokens = effective_context
        except Exception:
            pass
        event.set_extra("token_limit_context_tokens", effective_context)
        return True

    async def _cleanup_temp_context_provider_later(
        self,
        event: AstrMessageEvent,
        temp_provider_id: str,
    ) -> None:
        await asyncio.sleep(60)
        current_temp_provider_id = self._event_get_extra(
            event,
            "token_limit_temp_context_provider_id",
        )
        if str(current_temp_provider_id or "") == temp_provider_id:
            self._cleanup_temp_context_provider(event)

    @staticmethod
    def _provider_model_name(provider: Any, provider_config: dict[str, Any]) -> str:
        for key in ("model", "model_name"):
            model = str(provider_config.get(key) or "").strip()
            if model:
                return model
        get_model = getattr(provider, "get_model", None)
        if callable(get_model):
            try:
                return str(get_model() or "").strip()
            except Exception:
                return ""
        return ""

    @staticmethod
    def _provider_type_name(provider: Any, provider_config: dict[str, Any]) -> str:
        for key in ("type", "provider_type"):
            value = str(provider_config.get(key) or "").strip()
            if value:
                return value
        try:
            meta = provider.meta()
            return str(getattr(meta, "type", "") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _provider_api_base(provider_config: dict[str, Any]) -> str:
        for key in ("api_base", "base_url", "openai_api_base"):
            value = str(provider_config.get(key) or "").strip()
            if value:
                return value
        return ""

    def _provider_supports_prompt_cache_key(
        self,
        provider: Any,
        provider_config: dict[str, Any],
    ) -> bool:
        provider_type = self._provider_type_name(provider, provider_config).lower()
        if provider_type == "openai_chat_completion":
            return True
        module_name = str(getattr(provider.__class__, "__module__", "") or "").lower()
        return "openai" in module_name and "provider" in module_name

    @staticmethod
    def _model_is_gpt(model: str) -> bool:
        normalized_model = model.lower().strip()
        if not normalized_model:
            return False
        return (
            normalized_model.startswith("gpt-")
            or normalized_model.startswith("chatgpt-")
            or "/gpt-" in normalized_model
            or "/chatgpt-" in normalized_model
            or ":gpt-" in normalized_model
            or ":chatgpt-" in normalized_model
        )

    @staticmethod
    def _model_is_deepseek(model: str) -> bool:
        normalized_model = model.lower().strip()
        if not normalized_model:
            return False
        return (
            normalized_model.startswith("deepseek")
            or "/deepseek" in normalized_model
            or ":deepseek" in normalized_model
        )

    def _provider_is_deepseek(
        self,
        provider: Any,
        provider_config: dict[str, Any],
        model: str,
    ) -> bool:
        if self._model_is_deepseek(model):
            return True
        api_base = self._provider_api_base(provider_config).lower()
        if "deepseek" in api_base:
            return True
        provider_type = self._provider_type_name(provider, provider_config).lower()
        return "deepseek" in provider_type

    def _provider_supports_prompt_cache_retention(
        self,
        provider: Any,
        provider_config: dict[str, Any],
        model: str,
    ) -> bool:
        if not self._provider_supports_prompt_cache_key(provider, provider_config):
            return False
        api_base = self._provider_api_base(provider_config).lower().rstrip("/")
        if api_base and "api.openai.com" not in api_base:
            return False
        normalized_model = model.lower()
        return any(
            normalized_model.startswith(prefix)
            for prefix in TOKEN_LIMIT_OPENAI_CACHE_RETENTION_MODELS
        )

    @staticmethod
    def _prompt_cache_key_for_group(
        group_id: str,
        provider_id: str,
        model: str,
    ) -> str:
        raw_key = f"{group_id}|{provider_id}|{model}"
        digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
        return f"tl-{digest}"

    def _prompt_cache_extra_body(
        self,
        group_id: str,
        provider_id: str,
        provider: Any,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        model = self._provider_model_name(provider, provider_config)
        if not self._model_is_gpt(model):
            return {}
        cache_key = self._prompt_cache_key_for_group(group_id, provider_id, model)
        extra_body: dict[str, Any] = {"prompt_cache_key": cache_key}
        if self._provider_supports_prompt_cache_retention(
            provider,
            provider_config,
            model,
        ):
            extra_body["prompt_cache_retention"] = "24h"
        return extra_body

    def _apply_context_limit_provider_if_needed(
        self,
        event: AstrMessageEvent,
        limit_context: dict[str, Any],
    ) -> None:
        group_id = str(limit_context.get("group_id") or "")
        group_settings = self._load_group_settings()
        group_limits = {
            item_group_id: int(settings[GROUP_SETTING_DAILY_LIMIT])
            for item_group_id, settings in group_settings.items()
            if GROUP_SETTING_DAILY_LIMIT in settings
        }
        context_limit_tokens = self._group_context_limit_tokens(
            group_id,
            group_settings,
            group_limits,
        )
        gpt_prompt_cache_enabled = self._group_prompt_cache_key_strategy(
            group_id,
            group_settings,
        )
        deepseek_cache_enabled = self._group_deepseek_cache_strategy(
            group_id,
            group_settings,
        )
        if (
            context_limit_tokens <= 0
            and not gpt_prompt_cache_enabled
            and not deepseek_cache_enabled
        ):
            self._cleanup_temp_context_provider(event)
            return

        previous_selected_provider_id = str(
            self._event_get_extra(event, "selected_provider") or ""
        ).strip()
        provider_id = self._provider_id_for_context_limit(event, limit_context)
        if not provider_id:
            return
        provider = self.context.get_provider_by_id(provider_id)
        if provider is None or not hasattr(provider, "text_chat"):
            return
        provider_config = getattr(provider, "provider_config", None)
        if not isinstance(provider_config, dict):
            return
        prompt_cache_extra: dict[str, Any] = {}
        prompt_cache_key = ""
        prompt_cache_kind = ""
        model = self._provider_model_name(provider, provider_config)
        if gpt_prompt_cache_enabled and self._model_is_gpt(model):
            if self._provider_supports_prompt_cache_key(provider, provider_config):
                prompt_cache_extra = self._prompt_cache_extra_body(
                    group_id,
                    provider_id,
                    provider,
                    provider_config,
                )
                if prompt_cache_extra:
                    prompt_cache_key = str(prompt_cache_extra.get("prompt_cache_key") or "")
                    prompt_cache_kind = "gpt"
            if not prompt_cache_extra and context_limit_tokens <= 0:
                return
        elif (
            deepseek_cache_enabled
            and self._provider_is_deepseek(provider, provider_config, model)
        ):
            prompt_cache_key = self._prompt_cache_key_for_group(
                group_id,
                provider_id,
                model,
            )
            prompt_cache_kind = "deepseek"

        if context_limit_tokens <= 0 and not prompt_cache_kind:
            self._cleanup_temp_context_provider(event)
            return

        if context_limit_tokens <= 0 and not prompt_cache_extra:
            self._cleanup_temp_context_provider(event)
            event.set_extra("token_limit_prompt_cache_key", prompt_cache_key)
            event.set_extra("token_limit_prompt_cache_kind", prompt_cache_kind)
            event.set_extra("token_limit_prompt_cache_active", True)
            logger.debug(
                "Apply prompt cache anchor for group=%s provider=%s kind=%s",
                group_id,
                provider_id,
                prompt_cache_kind,
            )
            return

        self._cleanup_temp_context_provider(event)
        temp_provider = copy.copy(provider)
        temp_config = dict(provider_config)
        temp_provider_id = (
            f"{TOKEN_LIMIT_TEMP_PROVIDER_PREFIX}{group_id}_{provider_id}_{id(event)}"
        )
        if context_limit_tokens > 0:
            temp_config["max_context_tokens"] = context_limit_tokens
        if prompt_cache_extra:
            custom_extra_body = temp_config.get("custom_extra_body")
            merged_extra_body = (
                dict(custom_extra_body) if isinstance(custom_extra_body, dict) else {}
            )
            merged_extra_body.update(prompt_cache_extra)
            temp_config["custom_extra_body"] = merged_extra_body
        temp_provider.provider_config = temp_config
        if context_limit_tokens > 0:
            try:
                temp_provider.max_context_tokens = context_limit_tokens
            except Exception:
                pass

        provider_manager = getattr(self.context, "provider_manager", None)
        inst_map = getattr(provider_manager, "inst_map", None)
        provider_insts = getattr(provider_manager, "provider_insts", None)
        if not isinstance(inst_map, dict):
            return
        inst_map[temp_provider_id] = temp_provider
        if isinstance(provider_insts, list):
            provider_insts.append(temp_provider)

        event.set_extra("selected_provider", temp_provider_id)
        event.set_extra("token_limit_temp_context_provider_id", temp_provider_id)
        event.set_extra("token_limit_context_provider_origin", provider_id)
        event.set_extra("token_limit_context_tokens", context_limit_tokens)
        event.set_extra("token_limit_prompt_cache_key", prompt_cache_key)
        event.set_extra("token_limit_prompt_cache_kind", prompt_cache_kind)
        event.set_extra("token_limit_prompt_cache_active", bool(prompt_cache_kind))
        event.set_extra(
            "token_limit_context_provider_had_selected",
            bool(previous_selected_provider_id),
        )
        asyncio.create_task(
            self._cleanup_temp_context_provider_later(event, temp_provider_id)
        )
        log_method = logger.info if context_limit_tokens > 0 else logger.debug
        log_method(
            "Apply temporary provider overrides for group=%s provider=%s context=%s prompt_cache=%s retention=%s",
            group_id,
            provider_id,
            context_limit_tokens,
            prompt_cache_kind or "none",
            str(prompt_cache_extra.get("prompt_cache_retention") or ""),
        )

    @staticmethod
    def _context_limit_trim_budget(tokens: int) -> int:
        return max(1, int(max(1, tokens) * TOKEN_LIMIT_CONTEXT_TRIM_RATIO))

    @staticmethod
    def _context_limit_compress_threshold(tokens: int) -> int:
        return max(1, int(max(1, tokens) * TOKEN_LIMIT_CONTEXT_COMPRESS_THRESHOLD))

    @staticmethod
    def _context_limit_for_tokens(tokens: int) -> int:
        return max(
            1,
            math.ceil(max(1, tokens) / TOKEN_LIMIT_CONTEXT_COMPRESS_THRESHOLD) + 128,
        )

    @staticmethod
    def _estimate_text_tokens(value: Any) -> int:
        text = str(value or "")
        chinese_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        other_count = len(text) - chinese_count
        return int(chinese_count * 0.6 + other_count * 0.3)

    def _estimate_content_tokens(self, content: Any) -> int:
        if isinstance(content, str):
            return self._estimate_text_tokens(content)
        if isinstance(content, list):
            total = 0
            for item in content:
                if isinstance(item, dict):
                    item_type = str(item.get("type") or "")
                    if item_type in {"image_url", "image"}:
                        total += TOKEN_LIMIT_IMAGE_TOKEN_ESTIMATE
                    elif item_type in {"audio_url", "input_audio", "audio"}:
                        total += TOKEN_LIMIT_AUDIO_TOKEN_ESTIMATE
                    elif "text" in item:
                        total += self._estimate_text_tokens(item.get("text"))
                    else:
                        total += self._estimate_text_tokens(
                            json.dumps(item, ensure_ascii=False)
                        )
                    continue
                total += self._estimate_text_tokens(item)
            return total
        if isinstance(content, dict):
            return self._estimate_text_tokens(json.dumps(content, ensure_ascii=False))
        return self._estimate_text_tokens(content)

    def _estimate_request_messages_tokens(
        self,
        req: ProviderRequest,
        history_messages: list[Any],
    ) -> int:
        total = 0
        if getattr(req, "system_prompt", None):
            total += self._estimate_text_tokens(req.system_prompt)
        for message in history_messages:
            if isinstance(message, dict):
                total += self._estimate_content_tokens(message.get("content", ""))
                if message.get("tool_calls"):
                    total += self._estimate_text_tokens(
                        json.dumps(message.get("tool_calls"), ensure_ascii=False)
                    )
            else:
                total += self._estimate_text_tokens(message)
        if getattr(req, "prompt", None):
            total += self._estimate_text_tokens(req.prompt)
        for part in getattr(req, "extra_user_content_parts", None) or []:
            if hasattr(part, "text"):
                total += self._estimate_text_tokens(getattr(part, "text", ""))
            elif isinstance(part, dict):
                total += self._estimate_content_tokens(part)
            else:
                total += self._estimate_text_tokens(part)
        total += (
            len(getattr(req, "image_urls", None) or [])
            * TOKEN_LIMIT_IMAGE_TOKEN_ESTIMATE
        )
        total += (
            len(getattr(req, "audio_urls", None) or [])
            * TOKEN_LIMIT_AUDIO_TOKEN_ESTIMATE
        )
        return total

    @staticmethod
    def _drop_oldest_context_turn(messages: list[Any]) -> tuple[list[Any], int]:
        if not messages:
            return messages, 0

        first_user_index = 0
        for index, message in enumerate(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                first_user_index = index
                break

        next_user_index: int | None = None
        for index in range(first_user_index + 1, len(messages)):
            message = messages[index]
            if isinstance(message, dict) and message.get("role") == "user":
                next_user_index = index
                break

        drop_until = next_user_index if next_user_index is not None else len(messages)
        return messages[drop_until:], drop_until

    @staticmethod
    def _count_context_turns(messages: list[Any]) -> int:
        return sum(
            1
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        )

    @staticmethod
    def _keep_recent_context_turns(messages: list[Any], turns: int) -> list[Any]:
        if turns <= 0 or not messages:
            return []
        user_indexes = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        if len(user_indexes) <= turns:
            return list(messages)
        return list(messages[user_indexes[-turns] :])

    @staticmethod
    def _clear_request_conversation_token_usage(req: ProviderRequest) -> None:
        conversation = getattr(req, "conversation", None)
        if conversation is not None and hasattr(conversation, "token_usage"):
            try:
                conversation.token_usage = 0
            except Exception:
                pass

    def _trim_provider_request_context_if_needed(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        limit_context: dict[str, Any],
    ) -> None:
        group_id = str(limit_context.get("group_id") or "")
        context_limit_tokens = self._group_context_limit_tokens(group_id)
        if context_limit_tokens <= 0:
            return
        self._clear_request_conversation_token_usage(req)
        raw_history_messages = getattr(req, "contexts", None)
        history_messages = (
            raw_history_messages if isinstance(raw_history_messages, list) else []
        )
        budget = self._context_limit_trim_budget(context_limit_tokens)
        compress_threshold = self._context_limit_compress_threshold(
            context_limit_tokens
        )
        before_tokens = self._estimate_request_messages_tokens(req, history_messages)
        if before_tokens <= budget:
            return

        trimmed_messages = list(history_messages)
        removed_turns = 0
        while trimmed_messages:
            next_messages, removed_count = self._drop_oldest_context_turn(
                trimmed_messages
            )
            if removed_count <= 0 or len(next_messages) == len(trimmed_messages):
                break
            trimmed_messages = next_messages
            removed_turns += 1
            if self._estimate_request_messages_tokens(req, trimmed_messages) <= budget:
                break

        if len(trimmed_messages) == len(history_messages):
            after_tokens = before_tokens
        else:
            after_tokens = self._estimate_request_messages_tokens(
                req,
                trimmed_messages,
            )

        effective_context_tokens = context_limit_tokens
        raised_context = False
        if after_tokens > compress_threshold:
            fallback_messages = self._keep_recent_context_turns(
                history_messages,
                TOKEN_LIMIT_CONTEXT_FALLBACK_TURNS,
            )
            fallback_tokens = self._estimate_request_messages_tokens(
                req,
                fallback_messages,
            )
            trimmed_messages = fallback_messages
            after_tokens = fallback_tokens
            removed_turns = max(
                0,
                self._count_context_turns(history_messages)
                - self._count_context_turns(trimmed_messages),
            )
            if after_tokens > compress_threshold:
                effective_context_tokens = self._context_limit_for_tokens(after_tokens)
                raised_context = self._set_temp_context_provider_limit(
                    event,
                    effective_context_tokens,
                )

        if len(trimmed_messages) != len(history_messages):
            req.contexts = trimmed_messages
        if len(trimmed_messages) != len(history_messages) or raised_context:
            logger.info(
                "Token limit context trim group=%s configured=%s effective=%s tokens=%s->%s budget=%s removed_turns=%s kept_turns=%s raised=%s",
                group_id,
                context_limit_tokens,
                effective_context_tokens,
                before_tokens,
                after_tokens,
                budget,
                removed_turns,
                self._count_context_turns(trimmed_messages),
                raised_context,
            )

    def _apply_prompt_cache_anchor_if_needed(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        limit_context: dict[str, Any],
    ) -> None:
        if not bool(self._event_get_extra(event, "token_limit_prompt_cache_active")):
            return
        group_id = str(limit_context.get("group_id") or "")
        cache_key = str(self._event_get_extra(event, "token_limit_prompt_cache_key") or "")
        if not group_id or not cache_key:
            return
        current_prompt = str(getattr(req, "system_prompt", "") or "")
        if TOKEN_LIMIT_PROMPT_CACHE_ANCHOR in current_prompt:
            return
        req.system_prompt = f"{TOKEN_LIMIT_PROMPT_CACHE_ANCHOR}\n{current_prompt}"
        estimated_tokens = self._estimate_text_tokens(TOKEN_LIMIT_PROMPT_CACHE_ANCHOR)
        if estimated_tokens < TOKEN_LIMIT_PROMPT_CACHE_PREFIX_TARGET:
            logger.debug(
                "Prompt cache anchor estimate is below target: group=%s key=%s estimated=%s target=%s",
                group_id,
                cache_key,
                estimated_tokens,
                TOKEN_LIMIT_PROMPT_CACHE_PREFIX_TARGET,
            )

    @staticmethod
    def _event_get_extra(event: AstrMessageEvent, key: str) -> Any:
        get_extra = getattr(event, "get_extra", None)
        if not callable(get_extra):
            return None
        try:
            return get_extra(key)
        except Exception:
            return None

    @staticmethod
    def _event_truthy_attr(event: AstrMessageEvent, name: str) -> bool | None:
        value = getattr(event, name, None)
        if value is None:
            return None
        if callable(value):
            try:
                return bool(value())
            except TypeError:
                return None
            except Exception:
                return False
        return bool(value)

    @staticmethod
    def _event_self_id(event: AstrMessageEvent) -> str:
        get_self_id = getattr(event, "get_self_id", None)
        if callable(get_self_id):
            try:
                self_id = str(get_self_id() or "").strip()
                if self_id:
                    return self_id
            except Exception:
                pass

        self_id = str(getattr(event, "self_id", "") or "").strip()
        if self_id:
            return self_id

        message_obj = getattr(event, "message_obj", None)
        return str(getattr(message_obj, "self_id", "") or "").strip()

    @staticmethod
    def _event_group_id(event: AstrMessageEvent) -> str:
        def scalar_group_id(value: Any) -> str:
            if isinstance(value, (str, int, float)):
                return _normalize_group_id(value)
            return ""

        message_obj = getattr(event, "message_obj", None)
        for source in (message_obj, event):
            if source is None:
                continue
            for name in ("group_id", "room_id"):
                value = getattr(source, name, None)
                group_id = scalar_group_id(value)
                if group_id:
                    return group_id
            group_obj = getattr(source, "group", None)
            group_id = scalar_group_id(group_obj)
            if group_id:
                return group_id
            if isinstance(group_obj, dict):
                for name in ("group_id", "id", "uin"):
                    group_id = scalar_group_id(group_obj.get(name))
                    if group_id:
                        return group_id
            elif group_obj is not None:
                for name in ("group_id", "id", "uin"):
                    group_id = scalar_group_id(getattr(group_obj, name, None))
                    if group_id:
                        return group_id
        get_group_id = getattr(event, "get_group_id", None)
        if callable(get_group_id):
            try:
                group_id = _normalize_group_id(get_group_id())
                if group_id:
                    return group_id
            except Exception:
                pass
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if umo:
            group_id = _normalize_group_id(umo.rsplit(":", 1)[-1])
            if group_id:
                return group_id
        return ""

    @staticmethod
    def _message_component_type(item: Any) -> str:
        item_type = getattr(item, "type", "")
        item_type_value = getattr(item_type, "value", item_type)
        item_type_name = getattr(item_type, "name", "")
        values = {
            str(item_type_value or "").lower(),
            str(item_type_name or "").lower(),
            item.__class__.__name__.lower(),
        }
        return "at" if {"at", "componenttype.at"} & values else ""

    @staticmethod
    def _message_component_targets(item: Any) -> list[str]:
        targets = []
        for name in ("qq", "user_id", "id", "target", "uin"):
            value = getattr(item, name, None)
            if value is not None:
                targets.append(str(value).strip())

        for dump_name in ("model_dump", "dict"):
            dump = getattr(item, dump_name, None)
            if not callable(dump):
                continue
            try:
                data = dump()
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for name in ("qq", "user_id", "id", "target", "uin"):
                value = data.get(name)
                if value is not None:
                    targets.append(str(value).strip())
        return [target for target in targets if target]

    @staticmethod
    def _iter_message_items(event: AstrMessageEvent) -> list[Any]:
        candidates = []
        message_obj = getattr(event, "message_obj", None)
        if message_obj is not None:
            candidates.extend(
                [
                    getattr(message_obj, "message", None),
                    getattr(message_obj, "chain", None),
                ]
            )

        for getter_name in ("get_messages", "get_message_chain", "get_message"):
            getter = getattr(event, getter_name, None)
            if not callable(getter):
                continue
            try:
                candidates.append(getter())
            except Exception:
                continue

        items = []
        for candidate in candidates:
            if isinstance(candidate, (list, tuple)):
                items.extend(candidate)
            else:
                chain = getattr(candidate, "chain", None)
                message = getattr(candidate, "message", None)
                if isinstance(chain, (list, tuple)):
                    items.extend(chain)
                if isinstance(message, (list, tuple)):
                    items.extend(message)
        return items

    def _event_has_at_bot(self, event: AstrMessageEvent) -> bool:
        for name in ("is_at_bot", "is_at_self", "is_at"):
            value = self._event_truthy_attr(event, name)
            if value:
                return True

        self_id = self._event_self_id(event)
        for item in self._iter_message_items(event):
            if self._message_component_type(item) != "at":
                continue
            targets = self._message_component_targets(item)
            if not self_id or not targets:
                return True
            if any(target == self_id for target in targets):
                return True

        for getter_name in ("get_message_str", "get_raw_message"):
            getter = getattr(event, getter_name, None)
            if not callable(getter):
                continue
            try:
                text = str(getter() or "")
            except Exception:
                continue
            if "[CQ:at" in text or "<at" in text.lower():
                return not self_id or self_id in text

        message_obj = getattr(event, "message_obj", None)
        raw_message = str(getattr(message_obj, "raw_message", "") or "")
        if "[CQ:at" in raw_message or "<at" in raw_message.lower():
            return not self_id or self_id in raw_message
        return False

    def _is_wake_word_invocation(self, event: AstrMessageEvent) -> bool:
        if self._event_has_at_bot(event):
            return False

        for key in (
            "is_wake_command",
            "wake_command",
            "wake_word",
            "wake_prefix_matched",
            "is_at_or_wake_command",
        ):
            extra_value = self._event_get_extra(event, key)
            if extra_value:
                return True

        for name in (
            "is_wake_command",
            "wake_command",
            "wake_word",
            "wake_prefix_matched",
            "is_at_or_wake_command",
        ):
            value = self._event_truthy_attr(event, name)
            if value:
                return True
        return False

    def _should_block_wake_word_invocation(
        self,
        event: AstrMessageEvent,
        limit_context: dict[str, Any],
    ) -> bool:
        policy = limit_context["policy"]
        if not bool(policy.get("block_wake_words_after_limit")):
            return False
        limit = int(limit_context["limit"])
        used = int(limit_context["limit_state"]["used"])
        return limit > 0 and used >= limit and self._is_wake_word_invocation(event)

    def _should_block_group_only_at_bot_invocation(
        self,
        event: AstrMessageEvent,
    ) -> str | None:
        if not self._is_enabled():
            return None
        if not self._is_qq_group_event(event):
            return None
        group_id = self._event_group_id(event)
        if not group_id or group_id not in self._limited_groups():
            return None
        if not self._group_only_at_bot_llm(group_id):
            return None
        if not self._is_wake_word_invocation(event):
            return None
        return group_id

    def _block_group_only_at_bot_invocation_if_needed(
        self,
        event: AstrMessageEvent,
        stage: str,
    ) -> bool:
        group_id = self._should_block_group_only_at_bot_invocation(event)
        if not group_id:
            return False
        event.stop_event()
        logger.info(
            "Blocked wake-word invocation by group @bot-only policy: "
            "stage=%s group=%s",
            stage,
            group_id,
        )
        return True

    def _is_enabled(self) -> bool:
        return bool(self._config_value("enabled"))

    def _is_qq_group_event(self, event: AstrMessageEvent) -> bool:
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return False
        platform_names = self._qq_platform_names()
        return not platform_names or event.get_platform_name() in platform_names

    def _umo_candidates_for_group(self, group_id: str) -> list[str]:
        candidates = []
        for platform_id in self._qq_platform_ids():
            candidates.append(f"{platform_id}:{MessageType.GROUP_MESSAGE.value}:{group_id}")
        return candidates

    def _unique_session_like_patterns(self, group_id: str) -> list[str]:
        escaped_group_id = _escape_like(group_id)
        return [
            f"{_escape_like(platform_id)}:{MessageType.GROUP_MESSAGE.value}:%{escaped_group_id}%"
            for platform_id in self._qq_platform_ids()
        ]

    async def _query_usage_for_group(
        self,
        group_id: str,
        window: UsageWindow,
        provider_id: str | None = None,
        exclude_provider_id: str | None = None,
    ) -> tuple[int, dict[str, int]]:
        db = self.context.get_db()
        umo_candidates = self._umo_candidates_for_group(group_id)
        filters = [
            ProviderStat.agent_type == "internal",
            ProviderStat.created_at >= window.start_utc,
            ProviderStat.created_at < window.end_utc,
        ]
        if provider_id:
            filters.append(ProviderStat.provider_id == provider_id)
        if exclude_provider_id:
            filters.append(ProviderStat.provider_id != exclude_provider_id)

        if bool(self._config_value("match_unique_session")):
            umo_filter = ProviderStat.umo.in_(umo_candidates)
            for pattern in self._unique_session_like_patterns(group_id):
                umo_filter = umo_filter | ProviderStat.umo.like(
                    pattern,
                    escape="\\",
                )
            filters.append(umo_filter)
        else:
            filters.append(ProviderStat.umo.in_(umo_candidates))

        async with db.get_db() as session:
            hourly: dict[str, int] = {}
            total = 0
            database_url = str(getattr(db, "DATABASE_URL", "") or "").lower()
            if "sqlite" in database_url:
                bucket_expr = func.strftime(
                    "%Y-%m-%dT%H:00:00+00:00",
                    ProviderStat.created_at,
                )
                rows_result = await session.execute(
                    select(
                        bucket_expr.label("bucket"),
                        func.coalesce(func.sum(TOKEN_FIELDS_SUM), 0).label("tokens"),
                    )
                    .where(*filters)
                    .group_by(bucket_expr)
                    .order_by(bucket_expr.asc())
                )
                for bucket, tokens in rows_result.all():
                    if not bucket:
                        continue
                    bucket_utc = datetime.fromisoformat(str(bucket))
                    if bucket_utc.tzinfo is None:
                        bucket_utc = bucket_utc.replace(tzinfo=timezone.utc)
                    else:
                        bucket_utc = bucket_utc.astimezone(timezone.utc)
                    bucket_local = bucket_utc.astimezone(window.start_local.tzinfo)
                    normalized_tokens = int(tokens or 0)
                    hourly[bucket_local.isoformat()] = normalized_tokens
                    total += normalized_tokens
            else:
                total_result = await session.execute(
                    select(func.coalesce(func.sum(TOKEN_FIELDS_SUM), 0)).where(*filters)
                )
                total = int(total_result.scalar_one() or 0)

                rows_result = await session.execute(
                    select(ProviderStat.created_at, TOKEN_FIELDS_SUM.label("tokens"))
                    .where(*filters)
                    .order_by(col(ProviderStat.created_at).asc())
                )
                for created_at, tokens in rows_result.all():
                    created_at_utc = (
                        created_at.replace(tzinfo=timezone.utc)
                        if created_at.tzinfo is None
                        else created_at.astimezone(timezone.utc)
                    )
                    bucket = created_at_utc.astimezone(window.start_local.tzinfo).replace(
                        minute=0,
                        second=0,
                        microsecond=0,
                    )
                    key = bucket.isoformat()
                    hourly[key] = hourly.get(key, 0) + int(tokens or 0)
        return total, hourly

    async def _query_split_usage_for_group(
        self,
        group_id: str,
        window: UsageWindow,
        fallback_provider_id: str,
    ) -> tuple[int, int, dict[str, int]]:
        if not fallback_provider_id:
            primary_used, hourly = await self._query_usage_for_group(group_id, window)
            return primary_used, 0, hourly

        primary_used, hourly = await self._query_usage_for_group(
            group_id,
            window,
            exclude_provider_id=fallback_provider_id,
        )
        fallback_used, _ = await self._query_usage_for_group(
            group_id,
            window,
            provider_id=fallback_provider_id,
        )
        return primary_used, fallback_used, hourly

    @staticmethod
    def _build_limit_state(
        limit: int,
        policy: dict[str, Any],
        primary_used: int,
        fallback_used: int,
        fallback_configured: bool,
    ) -> dict[str, Any]:
        used = primary_used + fallback_used
        action = str(policy.get("action") or OVER_LIMIT_STOP)
        fallback_token_limit = max(0, int(policy.get("fallback_token_limit") or 0))
        hard_limit = limit
        effective_limit = limit
        using_fallback = False
        stopped = False
        status = "normal"

        if limit > 0:
            if action == OVER_LIMIT_FALLBACK and fallback_configured:
                hard_limit = limit + fallback_token_limit
                if used >= limit:
                    effective_limit = hard_limit
                    if used >= hard_limit:
                        stopped = True
                        status = "stopped"
                    else:
                        using_fallback = True
                        status = "fallback"
            elif used >= limit:
                stopped = True
                status = "stopped"

        percent = (
            0
            if effective_limit <= 0
            else min(100, round((used / effective_limit) * 100, 2))
        )
        return {
            "used": used,
            "hard_limit": hard_limit,
            "effective_limit": effective_limit,
            "percent": percent,
            "using_fallback": using_fallback,
            "stopped": stopped,
            "status": status,
        }

    async def _build_event_limit_context(
        self,
        event: AstrMessageEvent,
    ) -> dict[str, Any] | None:
        if not self._is_enabled():
            return None
        if not self._is_qq_group_event(event):
            return None

        group_id = self._event_group_id(event)
        if not group_id or group_id not in self._limited_groups():
            return None

        limit = self._daily_limit_for_group(group_id)
        if limit <= 0:
            return None

        window = _build_usage_window(self._config_value("refresh_time"))
        policy = self._over_limit_policy()
        fallback_provider_id = (
            str(policy["fallback_provider_id"])
            if policy["action"] == OVER_LIMIT_FALLBACK
            else ""
        )
        fallback_token_limit = int(policy["fallback_token_limit"])
        fallback_configured = bool(
            policy["action"] == OVER_LIMIT_FALLBACK and fallback_token_limit > 0
        )
        fallback_provider_valid = bool(
            fallback_provider_id and self._fallback_provider_exists(fallback_provider_id)
        )
        primary_used, fallback_used, _ = await self._query_split_usage_for_group(
            group_id,
            window,
            fallback_provider_id,
        )
        limit_state = self._build_limit_state(
            limit,
            policy,
            primary_used,
            fallback_used,
            fallback_configured,
        )
        return {
            "group_id": group_id,
            "limit": limit,
            "window": window,
            "policy": policy,
            "fallback_provider_id": fallback_provider_id,
            "fallback_token_limit": fallback_token_limit,
            "fallback_configured": fallback_configured,
            "fallback_provider_valid": fallback_provider_valid,
            "primary_used": primary_used,
            "fallback_used": fallback_used,
            "limit_state": limit_state,
        }

    async def _build_usage_payload(self) -> dict[str, Any]:
        groups = self._limited_groups()
        global_limit = self._daily_limit()
        group_settings = self._load_group_settings()
        group_limits = {
            group_id: int(settings[GROUP_SETTING_DAILY_LIMIT])
            for group_id, settings in group_settings.items()
            if GROUP_SETTING_DAILY_LIMIT in settings
        }
        policy = self._over_limit_policy()
        fallback_provider_id = (
            str(policy["fallback_provider_id"])
            if policy["action"] == OVER_LIMIT_FALLBACK
            else ""
        )
        fallback_token_limit = int(policy["fallback_token_limit"])
        fallback_configured = bool(
            policy["action"] == OVER_LIMIT_FALLBACK and fallback_token_limit > 0
        )
        fallback_enabled = bool(
            fallback_provider_id and self._fallback_provider_exists(fallback_provider_id)
        )
        window = _build_usage_window(self._config_value("refresh_time"))
        remarks = self._load_group_remarks()
        items = []
        for group_id in groups:
            limit = self._daily_limit_for_group(group_id, group_limits)
            has_custom_limit = group_id in group_limits
            only_at_bot_llm = self._group_only_at_bot_llm(group_id, group_settings)
            context_limit_05 = self._group_context_limit_05(group_id, group_settings)
            prompt_cache_key_strategy = self._group_prompt_cache_key_strategy(
                group_id,
                group_settings,
            )
            deepseek_cache_strategy = self._group_deepseek_cache_strategy(
                group_id,
                group_settings,
            )
            context_limit_tokens = self._group_context_limit_tokens(
                group_id,
                group_settings,
                group_limits,
            )
            primary_used, fallback_used, hourly = await self._query_split_usage_for_group(
                group_id,
                window,
                fallback_provider_id,
            )
            limit_state = self._build_limit_state(
                limit,
                policy,
                primary_used,
                fallback_used,
                fallback_configured,
            )
            used = int(limit_state["used"])
            effective_limit = int(limit_state["effective_limit"])
            hard_limit = int(limit_state["hard_limit"])
            items.append(
                {
                    "group_id": group_id,
                    "remark": remarks.get(group_id, ""),
                    "used_tokens": used,
                    "primary_used_tokens": primary_used,
                    "fallback_used_tokens": fallback_used,
                    "limit_tokens": effective_limit,
                    "hard_limit_tokens": hard_limit,
                    "primary_limit_tokens": limit,
                    "global_limit_tokens": global_limit,
                    "custom_limit_tokens": group_limits.get(group_id),
                    "has_custom_limit": has_custom_limit,
                    "only_at_bot_llm": only_at_bot_llm,
                    "context_limit_05": context_limit_05,
                    "prompt_cache_key_strategy": prompt_cache_key_strategy,
                    "deepseek_cache_strategy": deepseek_cache_strategy,
                    "context_limit_tokens": context_limit_tokens,
                    "context_limit_display": (
                        _format_context_limit_tokens(context_limit_tokens)
                        if context_limit_05
                        else _format_context_limit_tokens(
                            max(1, int(limit * TOKEN_LIMIT_CONTEXT_RATIO))
                        )
                    ),
                    "fallback_limit_tokens": fallback_token_limit,
                    "used_display": _format_tokens(used),
                    "limit_display": _format_tokens(effective_limit),
                    "hard_limit_display": _format_tokens(hard_limit),
                    "primary_limit_display": _format_tokens(limit),
                    "global_limit_display": _format_tokens(global_limit),
                    "custom_limit_display": (
                        _format_tokens(group_limits[group_id]) if has_custom_limit else ""
                    ),
                    "fallback_limit_display": _format_tokens(fallback_token_limit),
                    "percent": limit_state["percent"],
                    "limited": bool(limit_state["stopped"]),
                    "using_fallback": bool(
                        limit_state["using_fallback"] and fallback_enabled
                    ),
                    "fallback_unavailable": bool(
                        limit_state["using_fallback"] and not fallback_enabled
                    ),
                    "stopped": bool(limit_state["stopped"]),
                    "status": limit_state["status"],
                    "fallback_provider_id": fallback_provider_id,
                    "hourly": hourly,
                }
            )

        return {
            "enabled": self._is_enabled(),
            "groups": items,
            "remarks": remarks,
            "group_limits": group_limits,
            "group_settings": group_settings,
            "global_daily_token_limit": global_limit,
            "global_daily_token_limit_display": _format_tokens(global_limit),
            "over_limit_policy": {
                **policy,
                "fallback_configured": fallback_configured,
                "fallback_enabled": fallback_enabled,
            },
            "window": {
                "start": window.start_local.isoformat(),
                "end": window.end_local.isoformat(),
                "refresh_time": str(self._config_value("refresh_time") or "00:00"),
            },
        }

    def _serialize_config(self) -> dict[str, Any]:
        return {key: self._config_value(key) for key in CONFIG_SCHEMA}

    def _provider_options(self) -> list[dict[str, str]]:
        providers = []
        seen_provider_ids: set[str] = set()
        get_all_providers = getattr(self.context, "get_all_providers", None)
        provider_insts = get_all_providers() if callable(get_all_providers) else []
        for provider in provider_insts or []:
            if not hasattr(provider, "text_chat"):
                continue
            try:
                meta = provider.meta()
            except Exception:
                continue
            provider_id = str(getattr(meta, "id", "") or "").strip()
            if not provider_id or provider_id in seen_provider_ids:
                continue
            seen_provider_ids.add(provider_id)
            get_model = getattr(provider, "get_model", None)
            model = str(
                getattr(meta, "model", "")
                or (get_model() if callable(get_model) else "")
                or ""
            ).strip()
            provider_type = str(getattr(meta, "type", "") or "").strip()
            label_parts = [provider_id]
            detail = " / ".join(item for item in (provider_type, model) if item)
            if detail:
                label_parts.append(detail)
            providers.append(
                {
                    "id": provider_id,
                    "label": " - ".join(label_parts),
                    "type": provider_type,
                    "model": model,
                }
            )
        return providers

    def _config_schema_for_page(self) -> dict[str, dict[str, Any]]:
        schema = json.loads(json.dumps(CONFIG_SCHEMA, ensure_ascii=False))
        provider_options = self._provider_options()
        fallback_meta = schema["over_limit_policy"]["items"]["fallback_provider_id"]
        fallback_meta["options"] = [item["id"] for item in provider_options]
        fallback_meta["option_labels"] = [item["label"] for item in provider_options]
        return schema

    def _sanitize_config(self, raw_config: dict[str, Any]) -> dict[str, Any]:
        next_config = self._serialize_config()
        if "enabled" in raw_config:
            next_config["enabled"] = bool(raw_config["enabled"])
        if "limited_groups" in raw_config:
            next_config["limited_groups"] = self._normalize_config_list(
                raw_config["limited_groups"]
            )
        if "daily_token_limit" in raw_config:
            try:
                next_config["daily_token_limit"] = max(
                    0, int(raw_config["daily_token_limit"])
                )
            except (TypeError, ValueError):
                raise ValueError("单个群聊每日用量上限必须是整数。") from None
        if "user_daily_token_limit" in raw_config:
            try:
                next_config["user_daily_token_limit"] = max(
                    -1,
                    int(raw_config["user_daily_token_limit"]),
                )
            except (TypeError, ValueError):
                raise ValueError("单个用户每日用量上限必须是整数。") from None
        if "over_limit_policy" in raw_config:
            next_config["over_limit_policy"] = self._sanitize_over_limit_policy(
                raw_config["over_limit_policy"]
            )
        if "refresh_time" in raw_config:
            raw_time = str(raw_config["refresh_time"] or "").strip()
            if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", raw_time):
                raise ValueError("用量刷新时间格式必须是 HH:MM。")
            parsed = _parse_refresh_time(raw_time)
            next_config["refresh_time"] = f"{parsed.hour:02d}:{parsed.minute:02d}"
        if "qq_platform_names" in raw_config:
            next_config["qq_platform_names"] = self._normalize_config_list(
                raw_config["qq_platform_names"]
            )
        if "match_unique_session" in raw_config:
            next_config["match_unique_session"] = bool(
                raw_config["match_unique_session"]
            )
        if "block_message" in raw_config:
            next_config["block_message"] = str(raw_config["block_message"] or "")
        if "send_block_message" in raw_config:
            next_config["send_block_message"] = bool(raw_config["send_block_message"])
        return next_config

    def _sanitize_over_limit_policy(self, raw_policy: Any) -> dict[str, Any]:
        if not isinstance(raw_policy, dict):
            raw_policy = {}

        action = str(raw_policy.get("action") or OVER_LIMIT_STOP).strip()
        if action not in {OVER_LIMIT_STOP, OVER_LIMIT_FALLBACK}:
            raise ValueError("用量超限时的措施必须是“停止调用 LLM”或“回退到其他模型”。")

        fallback_provider_id = str(raw_policy.get("fallback_provider_id") or "").strip()
        try:
            fallback_token_limit = max(
                0,
                int(raw_policy.get("fallback_token_limit") or 0),
            )
        except (TypeError, ValueError):
            raise ValueError("回退模型的用量上限必须是整数。") from None

        if action == OVER_LIMIT_FALLBACK:
            if not fallback_provider_id:
                raise ValueError("选择“回退到其他模型”时必须配置回退的模型供应商。")
            if not self._fallback_provider_exists(fallback_provider_id):
                raise ValueError(f"未找到回退模型供应商：{fallback_provider_id}")

        return {
            "action": action,
            "fallback_provider_id": fallback_provider_id,
            "fallback_token_limit": fallback_token_limit,
            "block_wake_words_after_limit": bool(
                raw_policy.get("block_wake_words_after_limit", False)
            ),
        }

    @staticmethod
    def _normalize_config_list(value: Any) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in _split_group_values(value):
            text = _normalize_group_id(item)
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    async def api_get_config(self) -> dict:
        return _ok(
            {
                "config": self._serialize_config(),
                "schema": self._config_schema_for_page(),
            }
        )

    async def api_save_config(self) -> dict:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("请求体必须是 JSON 对象。")
        raw_config = payload.get("config", payload)
        if not isinstance(raw_config, dict):
            return _error("config 必须是 JSON 对象。")
        try:
            next_config = self._sanitize_config(raw_config)
        except ValueError as exc:
            return _error(str(exc))
        group_settings_backup = self._load_group_settings()
        self.config.clear()
        self.config.update(next_config)
        self._set_group_settings_config_backup(
            group_settings_backup,
            save_config=False,
        )
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            save_config()
        await self._maybe_sync_history_stats(force=True)
        await self._maybe_sync_user_stats(force=True)
        return _ok(
            {
                "config": self._serialize_config(),
                "schema": self._config_schema_for_page(),
            }
        )

    async def api_get_usage(self) -> dict:
        try:
            await self._maybe_sync_history_stats()
            await self._maybe_sync_user_stats()
            return _ok(await self._build_usage_payload())
        except Exception as exc:
            logger.error("获取 QQ 群 token 用量失败: %s", exc, exc_info=True)
            return _error(f"获取用量失败: {exc}")

    async def api_get_providers(self) -> dict:
        return _ok({"providers": self._provider_options()})

    async def api_get_remarks(self) -> dict:
        return _ok({"remarks": self._load_group_remarks()})

    async def api_save_remark(self) -> dict:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("Request body must be a JSON object.")

        group_id = _normalize_group_id(payload.get("group_id"))
        if not group_id:
            return _error("group_id is required.")

        remarks = self._load_group_remarks()
        remark = self._sanitize_group_remark(payload.get("remark"))
        if remark:
            remarks[group_id] = remark
        else:
            remarks.pop(group_id, None)

        try:
            self._save_group_remarks(remarks)
        except ValueError as exc:
            return _error(str(exc))

        return _ok({"group_id": group_id, "remark": remark, "remarks": remarks})

    async def api_get_group_settings(self) -> dict:
        group_id = _normalize_group_id(request.args.get("group_id"))
        if not group_id:
            return _error("group_id is required.")

        global_limit = self._daily_limit()
        group_settings = self._load_group_settings()
        group_limits = {
            item_group_id: int(settings[GROUP_SETTING_DAILY_LIMIT])
            for item_group_id, settings in group_settings.items()
            if GROUP_SETTING_DAILY_LIMIT in settings
        }
        has_custom_limit = group_id in group_limits
        effective_limit = self._daily_limit_for_group(group_id, group_limits)
        only_at_bot_llm = self._group_only_at_bot_llm(group_id, group_settings)
        context_limit_05 = self._group_context_limit_05(group_id, group_settings)
        prompt_cache_key_strategy = self._group_prompt_cache_key_strategy(
            group_id,
            group_settings,
        )
        deepseek_cache_strategy = self._group_deepseek_cache_strategy(
            group_id,
            group_settings,
        )
        context_limit_tokens = self._group_context_limit_tokens(
            group_id,
            group_settings,
            group_limits,
        )
        return _ok(
            {
                "group_id": group_id,
                "daily_token_limit": effective_limit,
                "daily_token_limit_display": _format_tokens(effective_limit),
                "global_daily_token_limit": global_limit,
                "global_daily_token_limit_display": _format_tokens(global_limit),
                "has_custom_limit": has_custom_limit,
                "custom_daily_token_limit": (
                    group_limits[group_id] if has_custom_limit else None
                ),
                "custom_daily_token_limit_display": (
                    _format_tokens(group_limits[group_id]) if has_custom_limit else ""
                ),
                "only_at_bot_llm": only_at_bot_llm,
                "context_limit_05": context_limit_05,
                "prompt_cache_key_strategy": prompt_cache_key_strategy,
                "deepseek_cache_strategy": deepseek_cache_strategy,
                "context_limit_tokens": context_limit_tokens,
                "context_limit_display": _format_context_limit_tokens(
                    context_limit_tokens
                    if context_limit_05
                    else max(1, int(effective_limit * TOKEN_LIMIT_CONTEXT_RATIO))
                ),
            }
        )

    async def api_save_group_settings(self) -> dict:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("Request body must be a JSON object.")

        group_id = _normalize_group_id(payload.get("group_id"))
        if not group_id:
            return _error("group_id is required.")

        group_settings = self._load_group_settings()
        group_limits = {
            item_group_id: int(settings[GROUP_SETTING_DAILY_LIMIT])
            for item_group_id, settings in group_settings.items()
            if GROUP_SETTING_DAILY_LIMIT in settings
        }
        if bool(payload.get("reset")):
            group_settings.setdefault(group_id, {}).pop(GROUP_SETTING_DAILY_LIMIT, None)
            if not group_settings[group_id]:
                group_settings.pop(group_id, None)
            try:
                self._save_group_settings(group_settings)
            except ValueError as exc:
                return _error(str(exc))

            group_limits.pop(group_id, None)
            global_limit = self._daily_limit()
            return _ok(
                {
                    "group_id": group_id,
                    "daily_token_limit": global_limit,
                    "daily_token_limit_display": _format_tokens(global_limit),
                    "global_daily_token_limit": global_limit,
                    "global_daily_token_limit_display": _format_tokens(global_limit),
                    "has_custom_limit": False,
                    "custom_daily_token_limit": None,
                    "custom_daily_token_limit_display": "",
                    "group_limits": group_limits,
                    "group_settings": group_settings,
                    "only_at_bot_llm": self._group_only_at_bot_llm(
                        group_id,
                        group_settings,
                    ),
                    "context_limit_05": self._group_context_limit_05(
                        group_id,
                        group_settings,
                    ),
                    "prompt_cache_key_strategy": self._group_prompt_cache_key_strategy(
                        group_id,
                        group_settings,
                    ),
                    "deepseek_cache_strategy": self._group_deepseek_cache_strategy(
                        group_id,
                        group_settings,
                    ),
                    "context_limit_tokens": self._group_context_limit_tokens(
                        group_id,
                        group_settings,
                        group_limits,
                    ),
                    "context_limit_display": _format_context_limit_tokens(
                        max(1, int(global_limit * TOKEN_LIMIT_CONTEXT_RATIO))
                    ),
                }
            )

        current_group_settings = dict(group_settings.get(group_id, {}))
        if "daily_token_limit" in payload:
            try:
                next_daily_limit = max(
                    0,
                    int(payload.get("daily_token_limit") or 0),
                )
            except (TypeError, ValueError):
                return _error("daily_token_limit must be an integer.")
            if (
                GROUP_SETTING_DAILY_LIMIT not in current_group_settings
                and next_daily_limit == self._daily_limit()
            ):
                current_group_settings.pop(GROUP_SETTING_DAILY_LIMIT, None)
            else:
                current_group_settings[GROUP_SETTING_DAILY_LIMIT] = next_daily_limit
        if "only_at_bot_llm" in payload:
            current_group_settings[GROUP_SETTING_ONLY_AT_BOT] = bool(
                payload.get("only_at_bot_llm")
            )
            if not current_group_settings[GROUP_SETTING_ONLY_AT_BOT]:
                current_group_settings.pop(GROUP_SETTING_ONLY_AT_BOT, None)
        if "context_limit_05" in payload:
            current_group_settings[GROUP_SETTING_CONTEXT_LIMIT_05] = bool(
                payload.get("context_limit_05")
            )
            if not current_group_settings[GROUP_SETTING_CONTEXT_LIMIT_05]:
                current_group_settings.pop(GROUP_SETTING_CONTEXT_LIMIT_05, None)
        if "prompt_cache_key_strategy" in payload:
            current_group_settings[GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY] = bool(
                payload.get("prompt_cache_key_strategy")
            )
            if not current_group_settings[GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY]:
                current_group_settings.pop(
                    GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY,
                    None,
                )
        if "deepseek_cache_strategy" in payload:
            current_group_settings[GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY] = bool(
                payload.get("deepseek_cache_strategy")
            )
            if not current_group_settings[GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY]:
                current_group_settings.pop(
                    GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY,
                    None,
                )

        if current_group_settings:
            group_settings[group_id] = current_group_settings
        else:
            group_settings.pop(group_id, None)
        try:
            self._save_group_settings(group_settings)
        except ValueError as exc:
            return _error(str(exc))

        global_limit = self._daily_limit()
        group_limits = {
            item_group_id: int(settings[GROUP_SETTING_DAILY_LIMIT])
            for item_group_id, settings in group_settings.items()
            if GROUP_SETTING_DAILY_LIMIT in settings
        }
        has_custom_limit = group_id in group_limits
        effective_limit = self._daily_limit_for_group(group_id, group_limits)
        context_limit_05 = self._group_context_limit_05(group_id, group_settings)
        context_limit_tokens = self._group_context_limit_tokens(
            group_id,
            group_settings,
            group_limits,
        )
        return _ok(
            {
                "group_id": group_id,
                "daily_token_limit": effective_limit,
                "daily_token_limit_display": _format_tokens(effective_limit),
                "global_daily_token_limit": global_limit,
                "global_daily_token_limit_display": _format_tokens(global_limit),
                "has_custom_limit": has_custom_limit,
                "custom_daily_token_limit": (
                    group_limits[group_id] if has_custom_limit else None
                ),
                "custom_daily_token_limit_display": (
                    _format_tokens(group_limits[group_id]) if has_custom_limit else ""
                ),
                "group_limits": group_limits,
                "group_settings": group_settings,
                "only_at_bot_llm": self._group_only_at_bot_llm(
                    group_id,
                    group_settings,
                ),
                "prompt_cache_key_strategy": self._group_prompt_cache_key_strategy(
                    group_id,
                    group_settings,
                ),
                "deepseek_cache_strategy": self._group_deepseek_cache_strategy(
                    group_id,
                    group_settings,
                ),
                "context_limit_05": context_limit_05,
                "context_limit_tokens": context_limit_tokens,
                "context_limit_display": _format_context_limit_tokens(
                    context_limit_tokens
                    if context_limit_05
                    else max(1, int(effective_limit * TOKEN_LIMIT_CONTEXT_RATIO))
                ),
            }
        )

    @filter.on_waiting_llm_request(priority=1000)
    async def on_waiting_llm_request(self, event: AstrMessageEvent) -> None:
        if self._block_group_only_at_bot_invocation_if_needed(
            event,
            "waiting_llm_request",
        ):
            return
        self._remember_user_usage_event(event)
        if await self._block_user_daily_limit_if_needed(event, "waiting_llm_request"):
            return
        await self._maybe_sync_history_stats()
        await self._maybe_sync_user_stats()
        limit_context = await self._build_event_limit_context(event)
        if not limit_context:
            self._cleanup_temp_context_provider(event)
            return

        self._cleanup_temp_context_provider(event)
        if self._should_block_wake_word_invocation(event, limit_context):
            event.stop_event()
            logger.info(
                "Blocked wake-word invocation after token limit: group=%s used=%s limit=%s",
                limit_context["group_id"],
                limit_context["limit_state"]["used"],
                limit_context["limit"],
            )
            return

        limit_state = limit_context["limit_state"]
        if limit_state["status"] != "fallback":
            if limit_state["status"] == "normal":
                self._apply_context_limit_provider_if_needed(event, limit_context)
            return

        fallback_provider_id = str(limit_context["fallback_provider_id"] or "")
        if not limit_context["fallback_provider_valid"]:
            self._apply_context_limit_provider_if_needed(event, limit_context)
            logger.warning(
                "群 %s 当前窗口 token=%s 已进入回退区间，但回退模型供应商 %s 不可用；本次将交由 AstrBot 使用当前可用供应商。",
                limit_context["group_id"],
                limit_state["used"],
                fallback_provider_id or "<empty>",
            )
            return

        event.set_extra("selected_provider", fallback_provider_id)
        event.set_extra("token_limit_selected_provider", fallback_provider_id)
        self._apply_context_limit_provider_if_needed(event, limit_context)
        logger.info(
            "群 %s 当前窗口 token=%s 已达每日上限 %s，未达硬上限 %s，本次预先切换到回退模型供应商 %s。",
            limit_context["group_id"],
            limit_state["used"],
            limit_context["limit"],
            limit_state["hard_limit"],
            fallback_provider_id,
        )

    @filter.on_llm_request(priority=1000)
    async def on_llm_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        conversation_id = (
            req.conversation.cid
            if getattr(req, "conversation", None) is not None
            else None
        )
        self._remember_user_usage_event(
            event,
            conversation_id=conversation_id,
            prompt=getattr(req, "prompt", None),
        )
        if self._block_group_only_at_bot_invocation_if_needed(event, "llm_request"):
            self._cleanup_temp_context_provider(event)
            return
        if await self._block_user_daily_limit_if_needed(event, "llm_request"):
            self._cleanup_temp_context_provider(event)
            return
        limit_context = await self._build_event_limit_context(event)
        if not limit_context:
            self._cleanup_temp_context_provider(event)
            return

        if self._should_block_wake_word_invocation(event, limit_context):
            self._cleanup_temp_context_provider(event)
            event.stop_event()
            logger.info(
                "Blocked wake-word LLM request after token limit: group=%s used=%s limit=%s",
                limit_context["group_id"],
                limit_context["limit_state"]["used"],
                limit_context["limit"],
            )
            return

        group_id = limit_context["group_id"]
        limit = int(limit_context["limit"])
        window = limit_context["window"]
        limit_state = limit_context["limit_state"]
        used = int(limit_state["used"])
        stop_limit = int(limit_state["hard_limit"])

        if limit_state["status"] in {"normal", "fallback"}:
            self._apply_prompt_cache_anchor_if_needed(event, req, limit_context)
            self._trim_provider_request_context_if_needed(event, req, limit_context)
        self._cleanup_temp_context_provider(event)

        if limit_state["status"] == "normal":
            return

        if limit_state["status"] == "fallback":
            fallback_provider_id = str(limit_context["fallback_provider_id"] or "")
            if limit_context["fallback_provider_valid"]:
                event.set_extra("selected_provider", fallback_provider_id)

            selected_provider = event.get_extra("token_limit_selected_provider")
            if selected_provider:
                logger.debug(
                    "群 %s 已预先切换到回退模型供应商 %s。",
                    group_id,
                    selected_provider,
                )
            elif limit_context["fallback_provider_valid"]:
                logger.warning(
                    "群 %s 已进入回退区间，但 provider 已在 LLM 请求钩子前完成选择；请确认 on_waiting_llm_request 钩子已启用。",
                    group_id,
                )
            else:
                logger.warning(
                    "群 %s 已进入回退区间，但回退模型供应商 %s 不可用；本次交由 AstrBot 当前可用供应商处理。",
                    group_id,
                    limit_context["fallback_provider_id"] or "<empty>",
                )
            return

        if bool(self._config_value("send_block_message")):
            message_template = str(self._config_value("block_message") or "")
            message = message_template.format(
                group_id=group_id,
                used=_format_tokens(used),
                limit=_format_tokens(stop_limit),
                refresh_time=self._config_value("refresh_time"),
                window_start=window.start_local.strftime("%Y-%m-%d %H:%M"),
                window_end=window.end_local.strftime("%Y-%m-%d %H:%M"),
            )
            if message:
                await event.send(MessageChain().message(message))

        event.stop_event()
        logger.info(
            "已拦截群 %s 的 LLM 请求：当前窗口 token=%s，上限=%s",
            group_id,
            used,
            stop_limit,
        )
