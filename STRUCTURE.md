# STRUCTURE

本文档记录 v0.6.6 当前插件结构、内部 API、页面元素映射和主要函数职责。历史版本增量已合并到当前结构说明中，不再按 v0.6.1 与 v0.6.2-v0.6.6 分段维护。

## 文件结构

```text
astrbot_plugin_token_controller/
├── main.py
├── metadata.yaml
├── _conf_schema.json
├── README.md
├── DEVELOP.md
├── STRUCTURE.md
├── LICENSE
├── backend/
│   ├── __init__.py
│   ├── history_stats.py
│   ├── user_limits.py
│   └── user_stats.py
└── pages/
    └── dashboard/
        └── index.html
```

运行时在 AstrBot 插件数据目录下生成：

```text
<AstrBot plugin data>/astrbot_plugin_token_controller/
├── group_remarks.json
├── group_limits.json
├── history_usage.json
└── user_usage_48h.json
```

- `group_remarks.json`：保存 QQ 群号到备注名的映射，不跟随 `limited_groups` 删除而删除。
- `group_limits.json`：保存群聊个性化配置，兼容旧格式 `{ "123456": 6000000 }`，保存时统一为对象结构。
- `history_usage.json`：保存历史 token 用量，包含每个被追踪群的 `tracked_since`、`last_synced_at`、`total_tokens` 和非零小时桶 `hours`。
- `user_usage_48h.json`：保存近 48 小时群内用户 token 用量、请求归因快照、用户昵称缓存和对话明细，超过保留期的数据会在同步时丢弃。

从旧插件名 `astrbot_plugin_token_limit` 升级时，插件会在新数据目录缺少对应文件时复制旧目录中的上述 JSON 文件，不删除旧目录，也不覆盖新目录中已经存在的文件。

`group_limits.json` 当前对象结构示例：

```json
{
  "123456": {
    "daily_token_limit": 6000000,
    "only_at_bot_llm": true,
    "context_limit_05": true,
    "prompt_cache_key_strategy": true,
    "deepseek_cache_strategy": true
  }
}
```

- `daily_token_limit` 缺省时表示该群继续使用全局每日上限。
- `only_at_bot_llm=true` 表示该群只允许 `@bot` 触发 LLM 回复，唤醒词触发会被静默阻断。
- `context_limit_05=true` 表示该群本次 LLM 请求的最大上下文窗口按有效每日额度的 0.5% 临时限制。
- `prompt_cache_key_strategy=true` 表示 GPT 专用稳定缓存键策略启用。
- `deepseek_cache_strategy=true` 表示 DeepSeek 专用稳定前缀策略启用。

## 元数据与配置

### metadata.yaml

- `name`：插件名称，当前为 `astrbot_plugin_token_controller`。
- `version`：当前版本 `0.6.6`。
- `repo`：插件仓库地址。
- `support_platforms`：默认支持 `aiocqhttp`、`qq_official`、`qq_official_webhook`。
- `pages`：声明 `dashboard` Plugin Page，页面文件为 `pages/dashboard/index.html`。

### _conf_schema.json

原生 WebUI 配置项：

| 配置项 | 功能 |
| --- | --- |
| `enabled` | 插件总开关。 |
| `limited_groups` | 需要限流的 QQ 群号列表；新群号会从加入时刻开始历史统计。 |
| `daily_token_limit` | 单群当前统计窗口内基础 token 上限。 |
| `user_daily_token_limit` | 单个群聊内单个用户当前统计窗口 token 上限；`-1` 表示不启用。 |
| `over_limit_policy.action` | 超限策略：`stop_llm` 或 `fallback_provider`。 |
| `over_limit_policy.fallback_provider_id` | 回退模型供应商 ID。 |
| `over_limit_policy.fallback_token_limit` | 回退模型额外 token 上限；硬上限为 `daily_token_limit + fallback_token_limit`。 |
| `over_limit_policy.block_wake_words_after_limit` | 超过群聊基础每日上限后是否阻断唤醒词触发；`@bot` 仍可继续进入回退或停止响应策略。 |
| `refresh_time` | 当前窗口刷新时间，按 AstrBot 机器本地时区计算。 |
| `qq_platform_names` | QQ 平台适配器名称白名单。 |
| `match_unique_session` | 是否兼容 `unique_session` 形式的 `umo`。 |
| `block_message` | 达到停止调用条件时发送的提示。 |
| `send_block_message` | 是否发送提示。 |

`main.py` 的 `CONFIG_SCHEMA` 与 `_conf_schema.json` 保持字段一致。Plugin Page 通过 `GET config` 获取运行时 schema，并动态注入回退供应商下拉选项。

## main.py

### 常量与数据结构

- `PLUGIN_NAME`：插件名称和 Web API 前缀，当前为 `astrbot_plugin_token_controller`。
- `LEGACY_PLUGIN_NAME`：旧插件名 `astrbot_plugin_token_limit`，用于首次改名后的运行时数据兼容复制。
- `GROUP_REMARKS_FILE` / `GROUP_LIMITS_FILE`：备注和群聊个性化配置文件名。
- `PLUGIN_DATA_FILES`：改名迁移时尝试复制的运行时 JSON 文件列表。
- `GROUP_SETTING_DAILY_LIMIT` / `GROUP_SETTING_ONLY_AT_BOT` / `GROUP_SETTING_CONTEXT_LIMIT_05` / `GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY` / `GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY`：群聊个性化配置字段名。
- `GROUP_SETTINGS_CONFIG_BACKUP_KEY`：Plugin Page 群聊个性化配置在 AstrBot 插件配置对象中的内部备份键，当前为 `_plugin_page_group_settings`。
- `TOKEN_LIMIT_CONTEXT_RATIO` / `TOKEN_LIMIT_CONTEXT_TRIM_RATIO` / `TOKEN_LIMIT_CONTEXT_COMPRESS_THRESHOLD` / `TOKEN_LIMIT_CONTEXT_FALLBACK_TURNS`：上下文窗口节约策略参数。
- `TOKEN_LIMIT_TEMP_PROVIDER_PREFIX`：临时 provider ID 前缀。
- `TOKEN_LIMIT_OPENAI_CACHE_RETENTION_MODELS` / `TOKEN_LIMIT_PROMPT_CACHE_ANCHOR_*`：缓存命中策略相关常量。
- `OVER_LIMIT_STOP` / `OVER_LIMIT_FALLBACK`：超限策略枚举值。
- `TOKEN_FIELDS_SUM`：AstrBot `ProviderStat` 中输入、缓存输入和输出 token 字段求和表达式。
- `CONFIG_SCHEMA`：后端配置 schema。
- `UsageWindow`：当前限流窗口，包含本地和 UTC 起止时间。

### 模块级工具函数

- `_ok()` / `_error()`：生成统一 API 响应。
- `_split_group_values()`：兼容 list、数字、逗号、空格、分号、中文标点和换行。
- `_normalize_group_id()`：标准化群号，兼容 `123.0`。
- `_escape_like()`：转义 SQL `LIKE` 通配符。
- `_format_tokens()` / `_format_context_limit_tokens()`：格式化 token 数为页面展示文本。
- `_prompt_cache_anchor_text()`：生成稳定缓存锚点文本。
- `_parse_refresh_time()`：解析 `HH:MM`。
- `_local_timezone()`：获取运行机器本地时区。
- `_build_usage_window()`：根据 `refresh_time` 生成当前 24 小时限流统计窗口。

### Main 初始化

`class Main(UserLimitMixin, UserStatsMixin, HistoryStatsMixin, Star)` 组合单用户限流、今日用户统计、历史统计 mixin 和 AstrBot `Star`。

`__init__(context, config)`：

- 保存 `Context` 和配置对象。
- 解析 `group_remarks.json`、`group_limits.json`、`history_usage.json` 与 `user_usage_48h.json` 路径。
- 初始化 `_history_sync_lock` 与 `_user_sync_lock`，避免多个请求并发同步同一持久化统计文件。
- 调用 `_ensure_history_tracking_for_current_groups()`，让当前配置中的群号在插件启动时开始历史追踪。
- 调用 `_ensure_user_tracking_for_current_groups()`，让当前配置中的群号在插件启动时建立今日用户统计结构。
- 注册 Web API：
  - `GET /astrbot_plugin_token_controller/config`
  - `POST /astrbot_plugin_token_controller/config`
  - `GET /astrbot_plugin_token_controller/usage`
  - `GET /astrbot_plugin_token_controller/history`
  - `GET /astrbot_plugin_token_controller/user-usage`
  - `GET /astrbot_plugin_token_controller/providers`
  - `GET /astrbot_plugin_token_controller/remarks`
  - `POST /astrbot_plugin_token_controller/remarks`
  - `GET /astrbot_plugin_token_controller/group-settings`
  - `POST /astrbot_plugin_token_controller/group-settings`

`initialize()` 启动历史统计与今日用户统计后台同步任务。`terminate()` 取消这些后台任务。

### 配置、备注与群设置

- `_resolve_group_remarks_path()`：优先使用 `StarTools.get_data_dir(PLUGIN_NAME)`，失败时回退插件目录。
- `_copy_legacy_data_files(data_dir)`：从旧插件数据目录复制缺失的运行时 JSON 到新数据目录；只复制、不覆盖、不删除。
- `_load_group_remarks()` / `_save_group_remarks()`：读写备注 JSON。
- `_sanitize_group_remark()`：裁剪备注，最大长度 64。
- `_resolve_group_limits_path()`：与备注文件同目录存放群聊个性化配置文件。
- `_normalize_group_settings_data()`：统一清洗对象结构和旧版 `{group_id: limit}` 结构。
- `_read_group_settings_file()` / `_write_group_settings_file()`：读写 `group_limits.json`。
- `_load_group_settings_config_backup()` / `_set_group_settings_config_backup()`：读写 AstrBot 插件配置对象中的内部备份，避免 Plugin Page 专属配置在重启或重装后失效。
- `_group_settings_fallback_paths()`：提供旧插件数据目录和插件目录 fallback 的群设置恢复路径。
- `_load_group_settings()` / `_save_group_settings()`：优先合并当前文件和配置备份；主文件不存在且无备份时才读取 fallback；保存时同时写 `group_limits.json` 与配置备份。
- `_load_group_limits()` / `_save_group_limits()`：保留兼容接口，只暴露群号到每日上限的映射；保存上限时保留已有策略开关。
- `_config_value()`：读取配置，缺省时使用 schema 默认值。
- `_serialize_config()`：输出完整配置快照。
- `_sanitize_config()`：校验并标准化所有配置项；`user_daily_token_limit` 最小值为 `-1`，`-1` 表示关闭单用户限流。
- `_sanitize_over_limit_policy()`：校验超限策略，回退策略必须填写且能找到供应商，并保存“超限后不再响应唤醒词”开关。
- `_normalize_config_list()`：标准化列表型配置。

### 限流目标识别

- `_limited_groups()`：读取并去重限流 QQ 群号。
- `_qq_platform_names()` / `_qq_platform_ids()`：读取 QQ 平台适配器名称集合，并从 AstrBot platform manager 解析平台实例 ID。
- `_is_enabled()`：判断插件是否启用。
- `_is_qq_group_event(event)`：判断是否为目标 QQ 平台群聊消息。
- `_event_get_extra(event, key)` / `_event_truthy_attr(event, name)`：兼容读取 AstrBot 事件数据。
- `_event_self_id(event)` / `_event_group_id(event)`：统一读取 bot 自身 ID 与群号；群聊级策略、单用户限流和用户统计复用同一群号判断。
- `_event_has_at_bot(event)`：尽量通过事件属性、消息链 At 组件和原始消息判断是否由 `@bot` 触发。
- `_is_wake_word_invocation(event)`：在非 `@bot` 触发前提下识别唤醒词触发事件。
- `_should_block_wake_word_invocation(event, limit_context)`：当开关启用且群聊用量达到有效基础上限时，判断本次唤醒词触发是否需要阻断。
- `_should_block_group_only_at_bot_invocation(event)` / `_block_group_only_at_bot_invocation_if_needed(event, stage)`：实现单群 `only_at_bot_llm` 静默阻断。
- `_umo_candidates_for_group(group_id)` / `_unique_session_like_patterns(group_id)`：生成 ProviderStat 会话匹配规则。

### 用量、回退和硬上限

- `_daily_limit()`：读取全局基础 token 上限。
- `_daily_limit_for_group(group_id, group_limits=None)`：读取某个群聊的有效基础上限；存在个性化上限时优先使用 `group_limits.json`，否则回退全局 `daily_token_limit`。
- `_over_limit_policy()`：合并并清洗超限策略，包括回退配置和唤醒词阻断开关。
- `_fallback_provider_id()` / `_fallback_token_limit()`：读取回退供应商和回退额度。
- `_fallback_provider_exists(provider_id)`：检查供应商存在且支持 `text_chat`。
- `_provider_options()`：从 `Context.get_all_providers()` 生成回退供应商选项。
- `_config_schema_for_page()`：向页面 schema 注入 `fallback_provider_id.options`。
- `_query_usage_for_group(group_id, window, provider_id=None, exclude_provider_id=None)`：查询 AstrBot 原生 `ProviderStat`，按当前 `umo` 匹配口径统计 token，返回窗口总量和小时桶。
- `_query_split_usage_for_group()`：配置回退供应商时拆分原始供应商用量和回退供应商用量；未配置时所有用量归入 `primary_used`。
- `_build_limit_state()`：统一计算 `normal`、`fallback`、`stopped` 状态、展示上限、硬上限和进度百分比。
- `_build_event_limit_context(event)`：为等待 LLM 和 LLM 请求钩子构造统一限流上下文。
- `_build_usage_payload()`：生成 Plugin Page 当前窗口用量数据，包括备注、群聊有效上限、个性化策略字段、状态、硬上限、回退标签所需字段和小时桶。

### 单群 token 节约策略

- `_group_only_at_bot_llm(group_id, group_settings=None)`：读取某群是否启用“仅通过 @bot 触发 LLM 回复”。
- `_group_context_limit_05(group_id, group_settings=None)`：读取某群是否启用 0.5% 上下文窗口策略。
- `_group_context_limit_tokens(group_id, group_settings=None, group_limits=None)`：按该群有效每日上限计算上下文窗口限制值。
- `_group_prompt_cache_key_strategy(group_id, group_settings=None)`：读取某群是否启用 GPT 稳定缓存键策略。
- `_group_deepseek_cache_strategy(group_id, group_settings=None)`：读取某群是否启用 DeepSeek 稳定前缀策略。
- `_provider_id_for_context_limit(event, limit_context)`：为上下文和缓存策略选择当前实际 provider，正常区间使用当前 provider，回退区间优先使用回退 provider。
- `_apply_context_limit_provider_if_needed(event, limit_context)`：为本次请求创建临时 provider 副本，可同时写入 `max_context_tokens`、`custom_extra_body.prompt_cache_key`，并记录 DeepSeek 稳定前缀事件标记。
- `_cleanup_temp_context_provider(event)` / `_cleanup_temp_context_provider_later(event, temp_provider_id)`：清理临时 provider 和 `token_limit_prompt_cache_*` 事件标记。
- `_trim_provider_request_context_if_needed(event, req, limit_context)`：仅裁剪当前 `ProviderRequest.contexts` 中的旧历史轮次，不写回持久化会话历史；必要时只抬高本次临时窗口。
- `_apply_prompt_cache_anchor_if_needed(event, req, limit_context)`：在本次 GPT 或 DeepSeek 请求的 `system_prompt` 开头插入稳定缓存锚点。
- `_model_is_gpt()` / `_model_is_deepseek()` / `_provider_is_deepseek()`：按模型名、API Base 或 provider 类型自动识别策略适用范围。
- `_prompt_cache_key_for_group()` / `_prompt_cache_extra_body()`：为 GPT 策略生成不暴露 QQ 群号明文的稳定缓存键和 OpenAI 兼容 extra body。

### Web API 函数

| 函数 | endpoint | 作用 |
| --- | --- | --- |
| `api_get_config()` | `GET config` | 返回 `{config, schema}`。 |
| `api_save_config()` | `POST config` | 保存配置，并强制同步历史统计和今日用户统计。 |
| `api_get_usage()` | `GET usage` | 节流同步历史统计和今日用户统计，并返回当前窗口用量。 |
| `api_get_history()` | `GET history` | 由 `HistoryStatsMixin` 提供，返回历史下拉菜单、总量和柱状图数据。 |
| `api_get_user_usage()` | `GET user-usage` | 由 `UserStatsMixin` 提供，返回某群 Top N 用户 token 用量排行和对话明细。 |
| `api_get_providers()` | `GET providers` | 返回可用回退供应商列表。 |
| `api_get_remarks()` | `GET remarks` | 返回备注映射。 |
| `api_save_remark()` | `POST remarks` | 保存或删除备注。 |
| `api_get_group_settings()` | `GET group-settings` | 返回某个群聊的有效每日上限、全局上限、个性化上限状态和所有单群策略开关。 |
| `api_save_group_settings()` | `POST group-settings` | 保存某个群聊的个性化每日上限和策略开关到 `group_limits.json`。 |

统一响应结构：

```json
{
  "status": "ok",
  "message": null,
  "data": {}
}
```

`usage` 与 `group-settings` 当前会返回 `only_at_bot_llm`、`context_limit_05`、`context_limit_tokens`、`context_limit_display`、`prompt_cache_key_strategy`、`deepseek_cache_strategy` 等字段。`POST group-settings` 的 `reset=true` 只重置 `daily_token_limit`，不会清除这些策略开关。

### LLM 请求钩子

- `on_waiting_llm_request(event)`：
  - 首先执行单群 `only_at_bot_llm` 阻断。
  - 调用 `_remember_user_usage_event()` 尽量缓存群内用户 QQ 昵称和请求归因快照。
  - 调用 `_block_user_daily_limit_if_needed()`；启用单用户上限且该用户在该群当前窗口用量达到上限时静默阻断，不进入回退模型，也不发送群聊提示。
  - 按节流规则同步历史统计和今日用户统计。
  - 在 AstrBot 选择 provider 之前计算限流状态。
  - 若 `block_wake_words_after_limit` 启用，且群聊用量达到有效基础上限，则阻断唤醒词触发；`@bot` 触发继续进入回退或停止响应规则。
  - 正常区间尝试应用上下文窗口、GPT 缓存键或 DeepSeek 稳定前缀策略。
  - 回退区间且回退供应商有效时写入 `selected_provider`，再按实际 provider 应用单群节约策略。
- `on_llm_request(event, req)`：
  - 补充带 `conversation_id` 和 `prompt` 的用户请求归因快照。
  - 再次执行单群 `only_at_bot_llm`、单用户限流和唤醒词阻断兜底。
  - `normal` 和 `fallback` 放行前执行稳定缓存锚点插入和上下文预裁剪。
  - 清理临时 provider；`fallback` 状态尽量保持或补写 `selected_provider`。
  - `stopped` 时按配置发送 `block_message` 并 `event.stop_event()`。

## backend/user_limits.py

`UserLimitMixin` 独立承载“单个用户每日用量上限”的判定逻辑。该 mixin 只在 `user_daily_token_limit >= 0` 时工作；默认 `-1` 不同步、不查询、不拦截，以节约资源。

- `_user_daily_limit()`：读取 `user_daily_token_limit`，非法值按 `-1` 处理。
- `_user_daily_limit_enabled()`：判断单用户限流是否启用。
- `_user_usage_total_for_event(event)`：仅处理已启用插件、目标 QQ 群聊、群号在 `limited_groups` 内且可获取发送者 ID 的 LLM 事件；强制同步该群今日用户统计，合并 ProviderStat 实时聚合结果和持久化归因小时桶。
- `_should_block_user_daily_limit(event)`：当用户当前窗口用量达到或超过上限时返回阻断上下文。
- `_block_user_daily_limit_if_needed(event, stage)`：静默阻断该用户本次 LLM 请求，仅写 AstrBot 日志，不发送 `block_message`，不切换回退供应商。

## backend/history_stats.py

`HistoryStatsMixin` 独立维护历史统计逻辑，避免 `main.py` 继续膨胀。

### 常量与工具

- `HISTORY_STATS_FILE`：持久化文件名 `history_usage.json`。
- `HISTORY_STATS_VERSION`：历史文件结构版本。
- `HISTORY_SYNC_OVERLAP`：同步回看窗口，当前 2 小时，用于修正 AstrBot 延迟写入。
- `HISTORY_SYNC_MIN_INTERVAL`：非强制同步最短间隔，当前 5 分钟。
- `HISTORY_BACKGROUND_SYNC_INTERVAL`：后台同步间隔，当前 3600 秒。
- `HISTORY_DEFAULT_TOP_LIMIT`：未选择群聊时展示 Top N，当前 10。
- `HISTORY_RANGE_KEYS`：`24h`、`7d`、`30d`、`all`。
- `HistoryUsageWindow`：历史查询窗口，字段与 `UsageWindow` 同名，可复用 `Main._query_usage_for_group()`。
- `_history_ok()` / `_history_error()`、`_format_history_tokens()`、`_hour_label()`、`_hour_range_label()`、`_day_range_label()` 等函数负责 API 响应和图表标签格式化。

### Mixin 函数

- `_resolve_history_stats_path()`：与备注文件同目录存放历史文件。
- `_load_history_stats()` / `_save_history_stats()`：读写历史 JSON。
- `_empty_history_stats()` / `_sanitize_history_stats()`：生成和清洗历史文件结构。
- `_ensure_history_tracking_for_current_groups(data=None)`：为当前 `limited_groups` 建立历史追踪；不删除旧群号。
- `_start_history_background_sync()` / `_stop_history_background_sync()`：在插件启停时管理历史同步后台任务。
- `_maybe_sync_history_stats(force=False)`：使用异步锁串行同步；非强制调用受 5 分钟节流保护，强制调用用于打开历史弹窗和保存配置。
- `_sync_history_group(group_id, group_data, now)`：查询 `ProviderStat`，用小时绝对桶覆盖最近同步窗口，避免重复累加。
- `api_get_history()`：返回历史统计下拉菜单、状态圆点数据、Top N 或趋势柱状图数据；选中群聊时额外返回 `range_total_tokens` 和 `range_total_display`。
- `_history_dropdown_groups()` / `_history_top_bars()` / `_history_group_bars()` / `_history_recent_hour_bars()` / `_history_recent_day_bars()` / `_history_all_bars()`：生成前端图表数据。

## backend/user_stats.py

`UserStatsMixin` 独立维护近 48 小时群内用户 token 用量统计，用于 Plugin Page 的“今日用户 token 用量统计”和“对话数据”弹窗。

### 常量与工具

- `USER_USAGE_STATS_FILE`：持久化文件名 `user_usage_48h.json`。
- `USER_USAGE_STATS_VERSION` / `USER_USAGE_ATTRIBUTION_VERSION`：用户统计与归因结构版本。
- `USER_USAGE_RETENTION` / `USER_USAGE_REQUEST_RETENTION`：用户小时桶和请求归因快照保留时间，当前 48 小时。
- `USER_USAGE_REQUEST_LOOKBACK`：把 ProviderStat 记录回配到最近一次用户请求的最大回看时间，当前 10 分钟。
- `USER_USAGE_REQUEST_FUTURE_TOLERANCE`：允许事件记录时间略晚于 provider `start_time` 的容差，当前 5 秒。
- `USER_USAGE_SYNC_OVERLAP` / `USER_USAGE_SYNC_MIN_INTERVAL` / `USER_USAGE_BACKGROUND_SYNC_INTERVAL`：同步窗口、节流和后台间隔。
- `USER_USAGE_DIALOG_PROMPT_LENGTH`：对话摘要长度，当前 20 字。
- `UserUsageWindow`：今日用户统计查询窗口，按 `refresh_time` 生成当前 24 小时统计周期。
- `_sanitize_dialog_prompt()`、`_sanitize_user_dialog()`、`_message_component_*()`：清洗用户输入摘要、引用消息和消息组件。
- `_extract_user_id_from_umo(umo, group_id)`：从常见 `umo` / `unique_session` 形式中解析群内用户 ID。

### Mixin 函数

- `_resolve_user_stats_path()`：与备注文件同目录存放 `user_usage_48h.json`。
- `_load_user_stats()` / `_save_user_stats()`：读写近 48 小时用户统计 JSON，保存时使用缩进格式。
- `_sanitize_user_stats()`：清洗历史文件，丢弃超过 48 小时的小时桶、请求归因快照和对话明细。
- `_ensure_user_tracking_for_current_groups(data=None)`：为当前 `limited_groups` 建立用户统计群结构。
- `_start_user_background_sync()` / `_stop_user_background_sync()`：在插件启停时管理用户统计后台任务。
- `_maybe_sync_user_stats(force=False, group_id=None)`：带锁同步用户统计；打开用户统计弹窗时可按群强制同步。
- `_sync_user_group(group_id, group_data, now)`：查询 `ProviderStat`，结合请求归因队列覆盖最近同步窗口内的用户小时桶和对话明细。
- `_assign_user_usage_records()` / `_match_user_usage_request()`：将只能定位到群会话的 ProviderStat 明细回配到最近的群内用户 LLM 请求。
- `_merge_user_hours()` / `_merge_user_dialogs()` / `_store_user_dialogs()`：合并和保存用户小时桶与对话明细。
- `_query_hourly_user_usage_for_group()` / `_query_user_totals_for_group()` / `_query_user_usage_details_for_group()`：查询并聚合用户维度用量和对话明细。
- `_stored_user_totals_for_group()` / `_stored_user_dialogs_for_group()` / `_combine_user_totals()` / `_combine_user_dialogs()`：合并实时查询结果和持久化归因结果。
- `_remember_user_usage_event(event, conversation_id=None, prompt=None)`：在 LLM 等待钩子和 LLM 请求钩子里缓存用户昵称、请求归因快照和用户输入摘要。
- `api_get_user_usage()`：返回群聊下拉菜单、当前窗口、同步时间，以及选中群的用户 Top N 横向柱状图数据。
- `_user_usage_rows()`：将用户 token 总量转为前端排行行，启用单用户上限时附带 `over_user_limit`。
- `_user_usage_dialog_rows()`：将对话明细格式化为 `prompt`、`tokens`、`display`、`time`、`created_at`。

## pages/dashboard/index.html

### 页面结构

- `.app` / `.layout`：页面根容器和两栏布局。
- 左侧 `.panel`：用量统计。
  - `#windowText`：当前窗口时间。
  - `#refreshBtn`：刷新当前窗口用量。
  - `#usageList`：群用量列表。
  - `.group-id` / `.group-remark`：QQ 群号和灰色括号备注。
  - 铅笔 `.icon-button`：打开备注编辑弹窗。
  - 齿轮 `.icon-button`：打开“群聊个性化配置”弹窗。
  - `.fallback-tag` / `.stop-tag`：黄色“回退模型”和红色“停止响应”标签。
  - `.usage-value.fallback` / `.usage-value.stopped`：黄色/红色用量值。
  - `.progress-fill.fallback` / `.progress-fill.stopped`：黄色/红色进度条。
- 右侧 `.panel`：插件功能。
  - `#openConfigBtn`：打开插件基础配置。
  - `#openStrategyBtn`：打开“用量超限策略配置”。
  - `#openHistoryBtn`：打开“历史 token 用量统计”。
  - `#openUserUsageBtn`：打开“今日用户 token 用量统计”。
  - `#statusLine`：插件启用状态。

### 弹窗元素

- `#overlay`：插件基础配置弹窗，`#configForm` 动态渲染除 `over_limit_policy` 外的配置项。
- `#strategyOverlay`：超限策略弹窗，`#strategyForm` 渲染 `over_limit_policy`；回退供应商和回退上限只在 `action=fallback_provider` 时显示。
- `#historyOverlay`：历史统计弹窗，包含 `#groupSelect`、`#rangeSelect`、`#historyRangeTotal`、`#historyYAxis`、`#historyChart`、`#historyXAxis` 和 `#historyFooter`。
- `#historyTooltip`：点击历史柱状图柱子后显示完整横坐标和 token 用量的气泡 tag。
- `#userUsageOverlay`：今日用户 token 用量统计弹窗，包含 `#userGroupSelect`、`#refreshUserUsageBtn`、`#userUsageWindow`、`#userUsageChart` 和 `#userUsageFooter`。
- `#userDialogOverlay`：点击用户柱状图后打开“对话数据”弹窗，包含 `#userDialogTarget`、`#userDialogTableBody`、`#sortUserDialogTokensBtn` 和 `#sortUserDialogTimeBtn`。
- `#remarkOverlay`：备注编辑弹窗。
- `#groupSettingsOverlay`：群聊个性化配置弹窗。
  - `#groupLimitInput`：该群聊每日 token 上限输入框，默认显示当前有效上限。
  - `#groupOnlyAtBotInput`：复选框“仅通过 @bot 触发 LLM 回复”。
  - `#groupContextLimitInput`：复选框“最大上下文窗口设置为额度的 0.5%”。
  - `#groupContextLimitValue`：显示限制后的 K/M 数值，例如 `(30K)`。
  - `#groupPromptCacheInput`：复选框“通过稳定缓存键提升缓存命中率”，旁边显示 `GPT 专用`。
  - `#groupDeepSeekCacheInput`：复选框“通过稳定前缀提升缓存命中率”，旁边显示 `DeepSeek 专用`。
  - `#groupSettingsHint` / `#groupSettingsToast`：来源说明和状态提示。
  - `.group-settings-section`、`.group-settings-checkbox`、`.cache-strategy-tag`、`.toast.warning`：分组、复选框、小字 tag 和黄色警告样式。

### 前端函数

- 通用：`setToast()`、`setStrategyToast()`、`setRemarkToast()`、`formatDate()`、`normalizeTextareaText()`、`normalizeListText()`、`parseListText()`。
- 配置渲染：`renderConfigControl()`、`appendConfigRow()`、`renderConfigForm()`、`renderStrategyForm()`。
- 当前用量：`renderUsage()`、`loadUsage()`。
- 群聊个性化配置：`openGroupSettings()`、`closeGroupSettings()`、`saveGroupSettings()`、`resetGroupSettings()`、`updateGroupContextLimitValue()`、`updateGroupContextLimitWarning()`、`updateGroupCacheStrategyWarning()`。
- 历史统计：`reloadHistory()`、`loadHistory()`、`renderHistoryControls()`、`renderHistoryTotal()`、`aggregateHistoryBars()`、`resolveHistoryBarsForViewport()`、`handleHistoryResize()`、`showHistoryTooltip()`、`hideHistoryTooltip()`、`renderHistoryChart()`。
- 今日用户统计：`loadUserUsage()`、`reloadUserUsage()`、`renderUserUsageControls()`、`renderUserUsageWindow()`、`renderUserUsageChart()`。
- 对话数据：`openUserDialog()`、`closeUserDialog()`、`sortUserDialog()`、`sortedUserDialogs()`、`renderUserDialog()`。
- 弹窗：`openConfig()`、`closeConfig()`、`openStrategy()`、`closeStrategy()`、`openHistory()`、`closeHistory()`、`openUserUsage()`、`closeUserUsage()`、`openRemark()`、`closeRemark()`。
- 保存：`saveConfig()`、`saveStrategy()`、`saveRemark()`、`saveGroupSettings()`。
- `init()`：等待 bridge ready 后加载配置和当前用量。

### 页面交互规则

- 备注显示在群号右侧，格式为 `（备注）`；铅笔图标跟在群号或备注右侧。
- 群聊个性化上限保存后立即影响当前用量进度条、回退/停止状态和 LLM 请求限流判断；其他群聊继续使用全局上限。
- `resetGroupSettings()` 只重置本群每日上限为全局值，不清除 `only_at_bot_llm`、`context_limit_05`、`prompt_cache_key_strategy` 或 `deepseek_cache_strategy`。
- 开启“仅通过 @bot 触发 LLM 回复”后，该群无论是否超限，非 `@bot` 的唤醒词触发都会被静默阻断。
- 开启“超限后不再响应唤醒词”后，群聊用量达到有效基础每日上限时，唤醒词触发不再进入 LLM 流程；`@bot` 触发仍继续执行回退模型或停止响应规则。
- 勾选上下文窗口策略后，括号中的限制值随“单独配置本群每日用量上限”输入框即时更新；限制值 `< 15K` 或 `>= 200K` 时显示黄色风险提示。
- 勾选任一缓存命中策略时，页面提示确认未同时启用会修改 LLM 请求体的其他插件；保存后重新打开不会仅因已保存状态显示该警告。
- GPT 策略和 DeepSeek 策略相互独立；模型不匹配时即使勾选也会跳过。
- 历史统计每次打开时恢复为“选择群聊 ...”和“近 24 小时”；未选择群聊时展示历史总 token Top N。
- 近 24 小时柱状图由页面按宽度动态选择 2、3 或 4 小时聚合；近一个月柱状图按 3 天聚合。
- 点击历史柱状图中的任一柱子会显示气泡 tag，第一行为横坐标，第二行为 token 用量值。
- 今日用户统计默认显示“选择群聊 ...”和操作提示；选中群聊后展示当前刷新周期内 token 消耗最高的 Top N 用户。
- 今日用户柱状图可点击打开对话数据弹窗；对话表格支持按用量或时间切换正序/倒序。
- 启用单用户上限且用户用量达到上限时，该用户柱子和 token 数值为红色。

## 配置与统计同步逻辑

- 当前窗口限流统计使用 `refresh_time` 切分 24 小时窗口。
- 群聊个性化上限只覆盖基础 `daily_token_limit`；回退模型额外上限仍来自全局 `over_limit_policy.fallback_token_limit`；唤醒词阻断阈值使用该群最终有效基础上限。
- 单用户上限 `user_daily_token_limit` 只按“某群内某用户”统计，不跨群合并；达到上限后静默阻断该用户发起的 LLM 请求，不使用回退模型，也不发送群聊提示。
- 历史统计不依赖 `refresh_time`；它以群号首次加入 `limited_groups` 的时间为起点，按小时桶持久化。
- 历史同步基于 AstrBot 原生 `ProviderStat`，保存的是每小时绝对桶值，不是累加 delta。
- 今日用户统计也基于 AstrBot 原生 `ProviderStat`；持久化文件保留近 48 小时群内用户小时桶、请求归因快照、对话明细和昵称缓存，不跨群合并同一个用户。
- 配置保存会强制同步一次历史统计和今日用户统计；页面 `usage` 和 LLM 等待钩子会节流同步。
- Plugin Page 群聊个性化配置不展示在普通配置页中，但会双写到 `group_limits.json` 和 AstrBot 插件配置对象的 `_plugin_page_group_settings` 内部备份；普通配置保存时会保留该备份。
- 启动或首次读取群设置时，插件优先合并配置备份与当前 `group_limits.json`；只有当前文件不存在且配置备份为空时，才从旧插件数据目录或插件目录 fallback 恢复。恢复结果会回填到当前持久化文件，避免重启或重装后 token 节约策略失效。
- 打开用户统计弹窗时会按选中群强制同步 ProviderStat 和请求归因数据。
- 旧群号不会因为从 `limited_groups` 移除而从 `history_usage.json` 删除，因此再次加入时历史总量和趋势可以继续显示。
- 单群上下文窗口策略只影响本次请求期间的临时 provider 副本和当前 `ProviderRequest.contexts`，不写回 AstrBot 供应商全局配置或持久化会话历史。
- 稳定缓存键策略只影响 GPT / ChatGPT 请求；DeepSeek 稳定前缀策略只影响 DeepSeek 请求。两者都只在 `normal` 或 `fallback` 放行路径执行。
