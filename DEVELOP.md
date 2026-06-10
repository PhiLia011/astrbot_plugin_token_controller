# DEVELOP

本文档记录每个版本对插件结构、配置、页面和运行逻辑的变动。

## 0.6.6 - 2026-06-10

新增单群 DeepSeek 专用缓存命中率优化策略，并将 GPT / DeepSeek 两类缓存策略拆分为相互独立的 per-group 开关。
- `metadata.yaml`
  - 版本号更新为 `0.6.6`。
  - 同版本维护：插件名从 `astrbot_plugin_token_limit` 安全改名为 `astrbot_plugin_token_controller`，版本号保持 `0.6.6`。
- `main.py`
  - 同版本维护：`PLUGIN_NAME` 更新为 `astrbot_plugin_token_controller`，Web API 前缀和 AstrBot 插件数据目录随新名称生效。
  - 同版本维护：新增 `LEGACY_PLUGIN_NAME` 与旧数据目录兼容复制逻辑；首次使用新插件数据目录时，只复制缺失的 `group_remarks.json`、`group_limits.json`、`history_usage.json`、`user_usage_48h.json`，不删除旧目录。
  - 同版本修复：新增 `GROUP_SETTINGS_CONFIG_BACKUP_KEY = "_plugin_page_group_settings"`，将 Plugin Page 的群聊个性化配置同时备份到 AstrBot 插件配置对象中；平台重启、插件重装或 `group_limits.json` 暂时不可用时，可从配置备份恢复 token 节约策略。
  - 同版本修复：`_load_group_settings()` 优先合并配置备份与当前 `group_limits.json`；仅当当前文件不存在且配置备份为空时，才从旧插件数据目录或插件目录 fallback 恢复，并在恢复后回填当前 `group_limits.json` 与配置备份；`api_save_config()` 保存普通配置时保留内部备份键，避免清空 Plugin Page 专属配置。
  - 新增 `GROUP_SETTING_DEEPSEEK_CACHE_STRATEGY = "deepseek_cache_strategy"`。
  - `_load_group_settings()` / `_save_group_settings()` / `_save_group_limits()` 会保留 `deepseek_cache_strategy` 布尔开关；保存该字段不会影响 `daily_token_limit`、`only_at_bot_llm`、`context_limit_05` 或 `prompt_cache_key_strategy`。
  - 新增 `_group_deepseek_cache_strategy()`，读取某群是否启用 DeepSeek 稳定前缀策略。
  - 新增 `_model_is_deepseek()` 与 `_provider_is_deepseek()`，按模型名、API Base 或 provider 类型自动判断 DeepSeek；GPT 策略继续只通过 `_model_is_gpt()` 生效。
  - `_apply_context_limit_provider_if_needed()` 会按当前实际 provider/model 自动分流：GPT 且开启 GPT 策略时合并 `custom_extra_body.prompt_cache_key`；DeepSeek 且开启 DeepSeek 策略时只激活稳定前缀，不发送 OpenAI 专属缓存参数。
  - `_cleanup_temp_context_provider()` 在没有临时 provider 的路径也会清理 `token_limit_prompt_cache_*` 事件标记，避免不同群聊或不同请求之间串状态。
  - `_event_group_id()` 支持 `event.group` / `message_obj.group` 本身为字符串或数字的边界情况，进一步降低群聊策略落到错误群号的风险。
  - `usage` 与 `group-settings` API 返回 `deepseek_cache_strategy`；`POST group-settings` 支持单独保存该字段。
- `pages/dashboard/index.html`
  - “群聊个性化配置”弹窗的“本群 token 节约策略”栏目新增 `#groupDeepSeekCacheInput` 复选框，显示“通过稳定前缀提升缓存命中率”与小字 tag“DeepSeek 专用”。
  - GPT 策略文案改为“通过稳定缓存键提升缓存命中率”，并使用同样的小字 tag“GPT 专用”。
  - 新增 `state.currentSettingsInitialDeepSeekCache` 和 `state.currentSettingsCacheWarningTouched`；打开弹窗时从 usage 缓存和 `group-settings` API 读取 DeepSeek 策略，保存时单独比较并提交。
  - 当用户在本次弹窗中勾选任一缓存策略时，`#groupSettingsToast` 显示黄色提示“警告：请确认没有同时启用会修改 LLM 请求体的其他插件。”；保存后重新打开不会仅因已保存勾选状态显示该提示。
- `README.md` / `STRUCTURE.md`
  - 同版本维护：将插件名、安装目录、仓库地址和 Web API 路径统一更新为 `astrbot_plugin_token_controller`。
  - 同版本维护：`STRUCTURE.md` 合并 v0.6.1 与 v0.6.2-v0.6.6 的分离结构介绍，改为当前 v0.6.6 单一结构说明。
  - 补充 v0.6.6 DeepSeek 稳定前缀策略、持久化字段、API 字段、页面元素映射和 provider/model 自动分流逻辑。

## 0.6.5 - 2026-06-09

新增单群“稳定缓存键策略”，用于提升支持 OpenAI Prompt Caching 的群聊连续对话缓存命中率。
- `metadata.yaml`
  - 版本号更新为 `0.6.5`。
- `main.py`
  - 新增 `GROUP_SETTING_PROMPT_CACHE_KEY_STRATEGY` 与 `TOKEN_LIMIT_OPENAI_CACHE_RETENTION_MODELS`。
  - 同版本修复：新增 `TOKEN_LIMIT_PROMPT_CACHE_ANCHOR_VERSION`、`TOKEN_LIMIT_PROMPT_CACHE_ANCHOR_LINES` 与稳定缓存锚点，避免只设置 `prompt_cache_key` 而请求前缀不足或不稳定导致 OpenAI `cached_tokens` 始终为 0。
  - `_load_group_settings()` / `_save_group_settings()` / `_save_group_limits()` 会保留 `prompt_cache_key_strategy` 布尔开关；旧版 `{group_id: limit}` 与现有 `daily_token_limit`、`only_at_bot_llm`、`context_limit_05` 结构继续兼容。
  - 新增 `_group_prompt_cache_key_strategy()`，读取某群是否启用稳定缓存键策略。
  - `_apply_context_limit_provider_if_needed()` 扩展为本次请求 provider 覆盖入口：同一个临时 provider 副本可同时写入 `max_context_tokens` 和 `custom_extra_body.prompt_cache_key`。
  - 新增 `_provider_model_name()`、`_provider_type_name()`、`_provider_api_base()`、`_provider_supports_prompt_cache_key()`、`_provider_supports_prompt_cache_retention()`、`_prompt_cache_key_for_group()` 与 `_prompt_cache_extra_body()`。
  - 同版本修复：新增 `_model_is_gpt()`，仅 GPT / ChatGPT 模型会启用缓存键和稳定前缀；DeepSeek 等非 GPT OpenAI-compatible 模型会自动跳过，避免强行发送 OpenAI GPT 缓存参数。
  - 同版本修复：新增 `_event_group_id()`，群聊级策略、单用户限流和用户统计统一使用同一套群号解析，优先读取 `message_obj.group_id`，再读取事件字段和 `unified_msg_origin` 兜底，降低策略应用到错误群聊的风险。
  - 同版本修复：新增 `_apply_prompt_cache_anchor_if_needed()`，在 `on_llm_request()` 的 `normal` / `fallback` 放行路径中，将稳定缓存锚点插入 `ProviderRequest.system_prompt` 开头；停止响应、单用户超限、唤醒词阻断不会执行该操作。
  - `prompt_cache_key` 使用 `group_id + provider_id + model` 的 SHA-256 短 hash 生成，不明文发送 QQ 群号；官方 OpenAI API Base 且 GPT-5 / GPT-4.1 系列会附加 `prompt_cache_retention=24h`。
  - 正常区间和回退区间均可应用该策略；停止响应、单用户超限、唤醒词阻断时不创建临时 provider。
  - `usage` 与 `group-settings` API 返回 `prompt_cache_key_strategy`，`POST group-settings` 支持单独保存该字段，且不会误触发每日上限独立配置。
- `backend/user_limits.py` / `backend/user_stats.py`
  - 同版本修复：读取群号时优先复用 `Main._event_group_id()`，使单用户限流、用户用量归因与群聊级个性化策略保持同一个目标群聊判断。
- `pages/dashboard/index.html`
  - “群聊个性化配置”弹窗的“本群 token 节约策略”栏目新增 `#groupPromptCacheInput` 复选框，显示“使用稳定缓存键策略提升缓存命中率”。
  - `openGroupSettings()` 从 usage 缓存和 `group-settings` API 读取缓存键策略。
  - `saveGroupSettings()` 单独比较并提交 `prompt_cache_key_strategy`，不会影响 `daily_token_limit`、`only_at_bot_llm` 或 `context_limit_05`。
- `README.md` / `STRUCTURE.md`
  - 补充 v0.6.5 稳定缓存键策略、持久化字段、API 字段、页面元素和临时 provider 覆盖逻辑。

## 0.6.4 - 2026-06-09

新增单群上下文窗口节约策略，可将某个群聊的 LLM 请求上下文窗口限制为该群有效每日额度的 0.5%。
- `metadata.yaml`
  - 版本号更新为 `0.6.4`。
- `main.py`
  - 新增 `GROUP_SETTING_CONTEXT_LIMIT_05`、`TOKEN_LIMIT_CONTEXT_RATIO`、`TOKEN_LIMIT_CONTEXT_COMPRESS_THRESHOLD`、`TOKEN_LIMIT_CONTEXT_FALLBACK_TURNS` 与 `TOKEN_LIMIT_TEMP_PROVIDER_PREFIX` 常量。
  - `_load_group_settings()` / `_save_group_settings()` 继续兼容旧结构，并保存 `context_limit_05` 布尔开关。
  - `_save_group_limits()` 保存每日上限时会保留 `only_at_bot_llm` 与 `context_limit_05`，避免两个个性化配置互相覆盖。
  - 新增 `_group_context_limit_05()` 与 `_group_context_limit_tokens()`，按单群有效每日上限实时计算 0.5% 上下文窗口。
  - 新增 `_provider_id_for_context_limit()`、`_apply_context_limit_provider_if_needed()`、`_cleanup_temp_context_provider()` 与 `_cleanup_temp_context_provider_later()`。
  - `on_waiting_llm_request()` 在 AstrBot 构建主 agent 前，为启用策略的群聊临时注册 provider 副本并写入 `selected_provider`；副本只修改本次请求的 `max_context_tokens`，不写回原 provider。
  - 回退区间会优先基于回退 provider 创建临时副本；正常区间基于当前 AstrBot 默认 provider 创建临时副本；停止响应状态不创建临时副本。
  - `on_llm_request()` 在清理 provider 注册表中的临时副本前，先完成历史上下文预裁剪和必要时的本次窗口抬高；清理只移除注册表临时项，不写回原 provider。
  - 新增 `_context_limit_trim_budget()`、`_context_limit_compress_threshold()`、`_context_limit_for_tokens()`、`_estimate_text_tokens()`、`_estimate_content_tokens()`、`_estimate_request_messages_tokens()`、`_drop_oldest_context_turn()`、`_keep_recent_context_turns()`、`_set_temp_context_provider_limit()`、`_clear_request_conversation_token_usage()` 与 `_trim_provider_request_context_if_needed()`；启用上下文窗口策略的群聊会先尽量按 80% 预算裁剪旧历史，若仍超过 AstrBot 压缩阈值则保留最近 3 轮并只抬高本次临时 provider 窗口，同时清理 `conversation.token_usage` 缓存。
  - `usage` 与 `group-settings` API 返回 `context_limit_05`、`context_limit_tokens`、`context_limit_display`。
- `pages/dashboard/index.html`
  - “群聊个性化配置”弹窗的“本群 token 节约策略”栏目新增 `#groupContextLimitInput` 复选框。
  - 复选框文字后新增 `#groupContextLimitValue`，以浅色小字显示当前额度 0.5% 对应的 K/M 数值。
  - 当勾选后限制值小于 `15K` 或大于等于 `200K` 时，`#groupSettingsToast` 显示黄色上下文窗口警告；保存中、重置中和错误提示优先。
  - `openGroupSettings()` 从 usage 缓存和 `group-settings` API 读取上下文窗口策略。
  - `saveGroupSettings()` 单独比较并提交 `context_limit_05`，不会误触发每日上限独立配置状态。
  - `groupLimitInput` 输入时实时刷新括号中的限制值。
- `README.md` / `STRUCTURE.md`
  - 补充 v0.6.4 单群上下文窗口策略、持久化字段、API 字段、页面元素和 LLM 钩子运行逻辑。

## 0.6.3 - 2026-06-09

扩展“今日用户 token 用量统计”，为用户排行补充有效 LLM 请求的对话明细。
- `metadata.yaml`
  - 版本号更新为 `0.6.3`。
- `backend/user_stats.py`
  - `user_usage_48h.json` 中每个用户新增 `dialogs` 列表，近 48 小时保留每条有效请求的 `stat_id`、`created_at`、`prompt` 和 `tokens`。
  - `_save_user_stats()` 改为缩进格式写入 JSON，便于管理员直接打开查看。
  - 新增 `_sanitize_dialog_prompt()`、`_sanitize_user_dialog()`、`_merge_user_dialogs()`、`_store_user_dialogs()`、`_stored_user_dialogs_for_group()`、`_combine_user_dialogs()` 与 `_user_usage_dialog_rows()`。
  - `_remember_user_usage_event()` 新增 `prompt` 参数；`on_llm_request()` 会把 `ProviderRequest.prompt` 传入请求快照，只记录用户原始输入摘要，不记录系统提示词。
  - `_query_hourly_user_usage_for_group()` 和 `_query_user_usage_details_for_group()` 在按用户统计 token 的同时生成对话明细；无法直接从 `umo` 解析用户的记录继续通过请求快照归因。
  - `api_get_user_usage()` 返回每个用户的 `dialogs`，供 Plugin Page 点击柱状图查看。
- `main.py`
  - `on_llm_request()` 调用 `_remember_user_usage_event(..., prompt=req.prompt)`，补齐当前 LLM 请求的用户输入摘要。
- `pages/dashboard/index.html`
  - 新增 `#userDialogOverlay` “对话数据”悬浮窗口。
  - 今日用户横向柱状图的柱子支持 hover/active 效果和点击打开对话表格。
  - 对话表格展示“对话 / 用量 / 时间”，默认按时间倒序；点击“用量”或“时间”表头可切换正序/倒序。
- `README.md` / `STRUCTURE.md`
  - 补充 v0.6.3 对话明细持久化结构、API 返回字段、页面元素映射和交互说明。
## 0.6.2 - 2026-06-08

新增单群“本群 token 节约策略”，允许管理员将某个群聊设置为仅通过 `@bot` 触发 LLM 回复。
- `metadata.yaml`
  - 版本号更新为 `0.6.2`。
- `main.py`
  - 新增 `GROUP_SETTING_DAILY_LIMIT` 与 `GROUP_SETTING_ONLY_AT_BOT` 常量。
  - 将 `group_limits.json` 的持久化读取扩展为兼容结构：旧版 `{group_id: limit}` 仍可读取，新版以 `{group_id: {daily_token_limit, only_at_bot_llm}}` 保存群聊个性化配置。
  - 新增 `_load_group_settings()` / `_save_group_settings()`，统一读写单群每日上限与“仅通过 @bot 触发 LLM 回复”开关。
  - 保留 `_load_group_limits()` / `_save_group_limits()` 兼容接口，确保现有每日上限限流逻辑不需要整体重写。
  - 新增 `_group_only_at_bot_llm()`、`_should_block_group_only_at_bot_invocation()` 与 `_block_group_only_at_bot_invocation_if_needed()`。
  - `on_waiting_llm_request()` 和 `on_llm_request()` 在单用户限流、群聊回退/停止策略之前先执行单群 `@bot-only` 策略；当事件是唤醒词触发且未包含 `@bot` 时静默 `event.stop_event()`。
  - 复用 `_is_wake_word_invocation()` 的边界判断：同一句消息同时包含 `@bot` 和唤醒词时不会被该策略阻断。
  - `api_get_group_settings()` 返回 `only_at_bot_llm`；`api_save_group_settings()` 支持保存 `only_at_bot_llm`。点击“重置”只删除该群个性化每日上限，不清除 `only_at_bot_llm`。
  - `_build_usage_payload()` 为每个群聊返回 `only_at_bot_llm` 与完整 `group_settings`，供 Plugin Page 弹窗即时渲染。
- `pages/dashboard/index.html`
  - “群聊个性化配置”弹窗在“单独配置本群每日用量上限”下方新增“本群 token 节约策略”栏目。
  - 新增 `#groupOnlyAtBotInput` 复选框，显示“仅通过 @bot 触发 LLM 回复”，默认不勾选。
  - `openGroupSettings()` 会先使用用量列表中的缓存值渲染，再通过 `group-settings` API 刷新准确状态。
  - `saveGroupSettings()` 同时比较每日上限和 `only_at_bot_llm`，任一变化都会提交；`resetGroupSettings()` 仍只重置每日上限。
- `README.md` / `STRUCTURE.md`
  - 补充 v0.6.2 的行为说明、持久化文件结构、后端函数、Web API 字段和页面元素映射。

## 0.6.1 - 2026-06-08

新增“单个用户每日用量上限”能力。

- `metadata.yaml`
  - 版本号更新为 `0.6.1`。
  - 描述补充群内用户用量限制和用户用量统计。
- `_conf_schema.json`
  - 在 `daily_token_limit` 下方新增 `user_daily_token_limit`，默认 `-1` 表示不限制单用户用量。
- `main.py`
  - `CONFIG_SCHEMA` 新增 `user_daily_token_limit`。
  - `_sanitize_config()` 校验该字段，允许最小值 `-1`。
  - 引入 `UserLimitMixin`，`Main` 继承改为 `class Main(UserLimitMixin, UserStatsMixin, HistoryStatsMixin, Star)`。
  - `on_waiting_llm_request()` 在群级回退/停止策略之前执行单用户上限静默阻断。
  - `on_llm_request()` 在补充 `conversation_id` 后再次执行单用户上限兜底阻断。
- `backend/user_limits.py`
  - 新增 `UserLimitMixin`，独立承载单用户上限读取、启用判断、当前用户用量查询和静默阻断。
  - 仅当 `user_daily_token_limit >= 0` 时启用判定；默认 `-1` 不触发额外用户用量查询。
  - 达到上限时只 `event.stop_event()` 并写 AstrBot 日志，不发送 `block_message`，不切换回退模型。
- `backend/user_stats.py`
  - `api_get_user_usage()` 返回 `user_daily_limit`、`user_daily_limit_enabled` 和显示值。
  - `_user_usage_rows()` 为超出单用户上限的用户行附加 `over_user_limit`，供页面标红。
- `pages/dashboard/index.html`
  - Plugin Page 基础配置弹窗会随 schema 在“单个群聊每日用量上限”下方渲染“单个用户每日用量上限”。
  - 今日用户 token 用量统计柱状图中，达到单用户上限的用户柱子和 token 数值变为红色。
- `README.md` / `STRUCTURE.md`
  - 补充单用户上限的配置、限流规则、静默阻断行为、页面高亮和 mixin 结构说明。

## 0.6.0 - 2026-06-08

新增“今日用户 token 用量统计”能力。

- `metadata.yaml`
  - 版本号更新为 `0.6.0`。
- `backend/user_stats.py`
  - 新增 `UserStatsMixin`，按 mixin 方式承载群内用户用量统计逻辑。
  - 新增持久化文件 `user_usage_48h.json`，保存近 48 小时的群内用户 token 小时桶、请求归因队列和尽量缓存到的 QQ 昵称。
  - 新增 `_query_hourly_user_usage_for_group()` 与 `_query_user_totals_for_group()`，基于 AstrBot 原生 `ProviderStat` 按用户聚合 token 用量。
  - 新增 `_extract_user_id_from_umo()`，从常见 `umo` / `unique_session` 形式中解析群内用户 ID；用户统计只在单群内计算，不跨群合并。
  - 新增 `_remember_user_usage_event()`，在 LLM 等待钩子和 LLM 请求钩子中尽量缓存用户昵称、请求归因快照和会话 ID；当 `ProviderStat.umo` 只有群会话 ID 时，刷新统计会按 provider `start_time` 回配到最近的群内用户请求，避免把整个群的用量混到单个用户。
  - 新增 `api_get_user_usage()`，返回群聊下拉菜单、当前刷新周期窗口和选中群的 Top N 用户横向柱状图数据。
- `main.py`
  - 引入 `UserStatsMixin`，`Main` 继承改为 `class Main(UserStatsMixin, HistoryStatsMixin, Star)`。
  - 初始化 `user_stats_path`、`_user_sync_lock` 与用户统计缓存结构。
  - 注册 `GET /astrbot_plugin_token_controller/user-usage` Web API。
  - 插件启停时同步启动/停止今日用户统计后台任务。
  - 保存配置、页面用量刷新和 LLM 等待钩子中加入用户统计同步；LLM 等待钩子会先缓存用户昵称，LLM 请求钩子会补充会话 ID 用于精确归因。
- `pages/dashboard/index.html`
  - “插件功能”中新增蓝色“今日用户 token 用量统计”按钮，位于“历史 token 用量统计”下方。
  - 新增 `#userUsageOverlay` 悬浮窗口。
  - 新增与历史统计相同交互风格的群聊自绘下拉菜单。
  - 新增“刷新”按钮；打开弹窗、选择群聊和点击刷新都会重新请求当前用户 token 用量。
  - 横向柱状图按最长纵坐标文字统一确定左侧起点，柱子紧贴标签右侧对齐。
  - 新增横向柱状图，展示选中群当前刷新周期内 token 消耗最高的 Top N 用户；0 用量用户不展示。
  - 新增 `renderUserUsageControls()`、`renderUserUsageWindow()`、`renderUserUsageChart()`、`loadUserUsage()`、`reloadUserUsage()`、`openUserUsage()` 和 `closeUserUsage()`。
- `README.md`
  - 补充今日用户 token 用量统计功能、Plugin Page 入口和近 48 小时持久化策略说明。
- `STRUCTURE.md`
  - 更新到 v0.6.0，补充 `backend/user_stats.py`、`user_usage_48h.json`、`user-usage` API、页面元素映射和函数职责。

## 0.5.1 - 2026-06-08

新增“超限后不再响应唤醒词”策略开关。

- `metadata.yaml`
  - 版本号更新为 `0.5.1`。
- `_conf_schema.json`
  - 在 `over_limit_policy` 默认值和配置项中新增 `block_wake_words_after_limit`，供 AstrBot 原生 WebUI 配置。
- `main.py`
  - `CONFIG_SCHEMA.over_limit_policy` 新增布尔配置 `block_wake_words_after_limit`。
  - `_over_limit_policy()` 和 `_sanitize_over_limit_policy()` 读写并保存该开关。
  - 新增 `_event_get_extra()`、`_event_truthy_attr()`、`_event_has_at_bot()`、`_is_wake_word_invocation()` 和 `_should_block_wake_word_invocation()`，用于兼容识别 `@bot` 与唤醒词触发方式。
  - `on_waiting_llm_request()` 在 AstrBot 选择 provider 前执行唤醒词阻断；当群聊当前窗口用量达到该群有效基础上限时，唤醒词触发直接 `event.stop_event()`。
  - `on_llm_request()` 增加同样的兜底判断；`@bot` 触发不会绕过限流，只会继续进入已有的回退模型或停止响应规则。
- `pages/dashboard/index.html`
  - “用量超限策略配置”弹窗新增“超限后不再响应唤醒词”开关。
  - 该开关始终显示；回退供应商和回退上限仍仅在选择“回退到其他模型”时显示。
- `STRUCTURE.md`
  - 更新到 v0.5.1，补充新配置项、事件识别函数、钩子行为和页面元素映射。

## 0.5.0 - 2026-06-08

新增群聊个性化每日上限配置，并增强历史柱状图点击详情。

- `metadata.yaml`
  - 版本号更新为 `0.5.0`。
- `main.py`
  - 新增持久化文件常量 `GROUP_LIMITS_FILE`，运行时生成 `group_limits.json`。
  - 新增 `_resolve_group_limits_path()`、`_load_group_limits()`、`_save_group_limits()`，按 QQ 群号保存个性化每日 token 上限。
  - 新增 `_daily_limit_for_group()`，统一读取群聊有效基础上限；存在个性化上限时覆盖全局 `daily_token_limit`。
  - `_build_event_limit_context()` 改为使用群聊有效上限，确保 LLM 请求限流立即按个性化配置执行。
  - `_build_usage_payload()` 返回每个群聊的 `global_limit_tokens`、`custom_limit_tokens`、`has_custom_limit` 和对应显示值，Plugin Page 可展示并编辑当前有效上限。
  - 注册 `GET /astrbot_plugin_token_controller/group-settings` 与 `POST /astrbot_plugin_token_controller/group-settings`。
  - 新增 `api_get_group_settings()` 和 `api_save_group_settings()`，用于读取/保存“群聊个性化配置”弹窗中的每日上限。
- `pages/dashboard/index.html`
  - 用量统计每个群聊的铅笔备注按钮后新增齿轮按钮，打开“群聊个性化配置”弹窗。
  - 新增 `#groupSettingsOverlay`、`#groupLimitInput`、`#saveGroupSettingsBtn`、`#cancelGroupSettingsBtn`、`#groupSettingsToast` 等页面元素。
  - 新增 `createGearIcon()`、`setGroupSettingsToast()`、`openGroupSettings()`、`closeGroupSettings()`、`saveGroupSettings()`。
  - 保存群聊上限后刷新用量列表，使进度条、回退状态和停止响应状态立即按新上限显示。
  - 历史柱状图柱子新增 hover/active 视觉反馈，并支持点击显示气泡 tag。
  - 新增 `#historyTooltip`、`showHistoryTooltip()`、`hideHistoryTooltip()`；气泡在鼠标点击位置显示横坐标和 token 用量，文本不省略，宽度随最长行自适应。
- `STRUCTURE.md`
  - 更新到 v0.5.0，补充 `group_limits.json`、新增 API、页面元素映射、函数职责、群聊个性化上限与 tooltip 交互规则。

## 0.4.1 - 2026-06-08

优化“历史 token 用量统计”弹窗的图表密度、下拉菜单状态展示和重新进入时的默认状态。

- `metadata.yaml`
  - 版本号更新为 `0.4.1`。
- `backend/history_stats.py`
  - `_format_history_tokens()` 增加小数位参数，支持近 24 小时和近一个月柱顶数值使用 1 位小数。
  - 新增 `_hour_range_label()` 和 `_day_range_label()`，用于多小时、多日聚合后的横坐标标签。
  - `api_get_history()` 在选中群聊时返回 `range_total_tokens` 和 `range_total_display`，供页面显示当前时间跨度内该群 token 用量总和。
  - `_history_recent_hour_bars()` 支持按多小时聚合；随后同版本迭代中，近 24 小时恢复返回 24 个小时原始桶，由页面按宽度动态聚合。
  - `_history_recent_day_bars()` 支持按多日聚合；近一个月从 30 根日柱调整为 10 根 3 天柱，并使用英文月份缩写显示横坐标。
- `pages/dashboard/index.html`
  - “插件功能”按钮顺序调整为：黄色“插件基础配置 ...”在最上方，其下为“用量超限策略配置”和“历史 token 用量统计”。
  - 历史统计弹窗的工具栏新增 `#historyRangeTotal` 右侧总量显示区域。
  - 新增 `renderHistoryTotal()`，根据 `history` API 返回值展示选中群聊在当前时间跨度内的 token 总量；未选群聊时保持为空。
  - 新增 `aggregateHistoryBars()`、`resolveHistoryBarsForViewport()` 和 `handleHistoryResize()`，近 24 小时图表优先使用 2 小时聚合，空间不足时依次降为 3 小时、4 小时聚合。
  - 群聊下拉菜单中状态圆点移动到每日 token 用量数字前方，不再放在群号前方。
  - 每次打开历史统计弹窗时重置为“选择群聊 ...”和“近 24 小时”，并恢复展示历史总量 Top N。
  - 切换群聊或时间跨度时会清空旧总量占位，避免加载期间短暂显示过期数据。
- `STRUCTURE.md`
  - 更新到 v0.4.1，补充历史统计 API 返回字段、图表聚合粒度、`#historyRangeTotal` 元素映射和弹窗重置规则。

## 0.4.0 - 2026-06-08

新增“历史 token 用量统计”能力，并把历史统计后端拆分到独立 mixin。

- `metadata.yaml`
  - 版本号更新为 `0.4.0`。
- `_conf_schema.json`
  - 补充 `limited_groups` 提示：新加入群号会从加入时刻开始历史统计，移除后仍继续统计。
  - 补充 `match_unique_session` 提示：当前窗口与历史统计均使用该匹配口径。
- `backend/`
  - 新增后端目录，按 mixin 方式承载可独立演进的后端能力。
- `backend/__init__.py`
  - 新增包初始化文件。
- `backend/history_stats.py`
  - 新增 `HistoryStatsMixin`。
  - 新增历史持久化文件 `history_usage.json`，保存 `tracked_since`、`last_synced_at`、`total_tokens` 和小时桶。
  - 新增 `_ensure_history_tracking_for_current_groups()`：首次发现 `limited_groups` 中的新群号时建立追踪记录。
  - 新增 `_maybe_sync_history_stats()`：通过异步锁串行同步；非强制同步按 5 分钟节流并复用内存缓存，强制同步用于配置保存和历史弹窗。
  - 新增 `_sync_history_group()`：从 AstrBot `ProviderStat` 查询每小时绝对桶值，覆盖最近 2 小时窗口，避免重复累加并修正延迟写入。
  - 新增 `api_get_history()`：返回历史统计下拉菜单、状态圆点数据、Top N 或趋势柱状图数据。
  - 新增按小时、按日、按月的历史桶聚合函数。
- `main.py`
  - 引入 `HistoryStatsMixin`，`Main` 继承改为 `class Main(HistoryStatsMixin, Star)`。
  - 初始化时解析 `history_stats_path` 并为当前 `limited_groups` 建立历史追踪。
  - 新增 `initialize()` / `terminate()`，插件启用时启动每小时后台历史同步，禁用或重载时取消任务。
  - 注册 `GET /astrbot_plugin_token_controller/history` Web API。
  - `api_save_config()` 保存配置后强制同步历史统计，确保新群号立即拥有追踪起点。
  - `api_get_usage()` 和 `on_waiting_llm_request()` 增加节流历史同步，使已追踪群即使后续从限流列表移除也能继续补齐统计。
- `pages/dashboard/index.html`
  - 在“插件功能”中新增蓝色“历史 token 用量统计”按钮，位于“用量超限策略配置”下方。
  - 新增 `#historyOverlay` 历史统计弹窗。
  - 新增自绘群聊下拉菜单：展示当前限流群号、备注、每日 token 用量和绿/黄/红状态圆点。
  - 新增自绘时间跨度下拉菜单：`近 24 小时`、`近 7 天`、`近一个月`、`历史总和`。
  - 新增动态柱状图：未选择群聊时展示历史总量 Top N；选择群聊后展示对应时间跨度趋势。
  - 柱状图横纵坐标随数据变化，柱顶显示数值，并使用高度过渡动画切换数据。
- `README.md`
  - 新增历史统计功能、持久化策略和 Plugin Page 入口说明。
- `STRUCTURE.md`
  - 更新到 v0.4.0，补全后端 mixin、历史数据文件、`history` API、页面元素映射和函数职责。

## 0.3.1 - 2026-06-07

修复回退策略下配置变更后硬上限失效的问题。

- `metadata.yaml`
  - 版本号更新为 `0.3.1`。
- `_conf_schema.json`
  - 更新 `daily_token_limit` 和 `fallback_token_limit` 提示，明确当前统计窗口内总用量与硬上限规则。
- `main.py`
  - 新增 `_query_split_usage_for_group()`，集中拆分原始供应商用量和回退供应商用量。
  - 新增 `_build_limit_state()`，统一计算 `normal`、`fallback`、`stopped` 三种状态。
  - 新增 `_build_event_limit_context()`，让等待 LLM 阶段和 LLM 请求阶段共用同一套限流上下文。
  - 新增 `on_waiting_llm_request()`，在 AstrBot 选择 provider 和构建 agent 之前写入 `selected_provider`，修复回退区间仍使用原始模型的问题。
  - 修复请求判断口径：
    - `used < daily_token_limit` 时使用原始模型。
    - 选择回退策略时，`daily_token_limit <= used < daily_token_limit + fallback_token_limit` 进入回退区间。
    - 回退供应商有效时强制使用回退供应商；回退供应商失效时交由 AstrBot 使用当前可用供应商。
    - `used >= hard_limit` 时必须阻止该群所有 LLM 调用。
    - 选择停止调用策略时，`used >= daily_token_limit` 必须阻止该群所有 LLM 调用。
  - `on_llm_request()` 保留硬上限兜底拦截，不再承担 provider 选择职责，因为该钩子触发时 AstrBot 已完成 provider 选择。
  - `_build_usage_payload()` 改为使用同一套状态计算逻辑，返回 `stopped`、`status`、`hard_limit_tokens`、`hard_limit_display`。
  - 硬上限随当前配置实时变化，用户调低上限后下一次请求立即生效。
- `pages/dashboard/index.html`
  - 新增 `.stop-tag`、`.usage-value.stopped`、`.progress-fill.stopped`。
  - 用量统计中达到硬上限时显示红色“停止响应”标签。
  - 达到硬上限时 token 用量值字体变红，进度条变红。
  - 红色停止状态优先于黄色回退状态。
- `STRUCTURE.md`
  - 更新到 v0.3.1，补全硬上限规则、三态状态机、页面红色状态映射和新增函数职责。

## 0.3.0 - 2026-06-07

新增“用量超限时的措施”能力。

- `metadata.yaml`
  - 版本号更新为 `0.3.0`。
- `_conf_schema.json`
  - 新增 `over_limit_policy` 配置集合。
  - 集合内包含 `action`、`fallback_provider_id`、`fallback_token_limit`。
  - `daily_token_limit` 和 `block_message` 文案调整为同时适配停止调用与回退策略。
- `main.py`
  - 新增策略常量 `OVER_LIMIT_STOP`、`OVER_LIMIT_FALLBACK`。
  - 新增 `over_limit_policy` 到 `CONFIG_SCHEMA`。
  - 新增 `/providers` Web API，用于 Plugin Page 获取当前可作为回退模型的供应商列表。
  - 新增 `_over_limit_policy()`、`_fallback_provider_id()`、`_fallback_token_limit()`、`_fallback_provider_exists()`。
  - 新增 `_provider_options()`、`_config_schema_for_page()`，让 Plugin Page 的回退供应商字段可动态渲染下拉选项。
  - 扩展 `_query_usage_for_group()`，支持按 `provider_id` 统计或排除某个供应商。
  - 扩展 `_build_usage_payload()`，返回原始模型用量、回退模型用量、回退上限、回退状态和有效总上限。
  - 扩展 `_sanitize_config()` 和新增 `_sanitize_over_limit_policy()`，保存配置时校验回退策略。
  - 扩展 `on_llm_request()`：
    - 原始模型未超限时保持默认供应商。
    - 原始模型超限且回退额度未耗尽时，通过 `event.set_extra("selected_provider", fallback_provider_id)` 切换供应商。
    - 回退额度耗尽或策略为停止调用时，发送拦截提示并 `event.stop_event()`。
- `pages/dashboard/index.html`
  - 在“插件功能”中新增蓝色“用量超限策略配置”按钮。
  - 新增 `#strategyOverlay`、`#strategyForm`、`#saveStrategyBtn`、`#cancelStrategyBtn`、`#strategyToast`。
  - 抽象配置控件渲染函数，普通配置弹窗和策略弹窗共用同一套 schema 渲染逻辑。
  - 策略弹窗只在选择“回退到其他模型”时显示回退供应商和回退上限。
  - 用量统计在回退阶段显示黄色进度条、黄色用量文本和“回退模型”标签。
  - 回退阶段的显示上限改为 `daily_token_limit + fallback_token_limit`。
- `README.md`
  - 更新功能说明、配置项、超限策略示例和兼容性说明。
- `STRUCTURE.md`
  - 重写为 v0.3.0 结构文档，补充完整文件结构、Web API、HTML 元素映射和 `main.py` 函数职责。

## 0.2.0 - 2026-06-07

增强 Plugin Page 的可读性和可编辑性。

- `main.py`
  - 新增群备注持久化文件 `group_remarks.json`。
  - 新增 `GROUP_REMARKS_FILE`、`MAX_GROUP_REMARK_LENGTH`。
  - 新增 `_resolve_group_remarks_path()`、`_load_group_remarks()`、`_save_group_remarks()`、`_sanitize_group_remark()`。
  - 新增 `/remarks` GET/POST Web API。
  - `_build_usage_payload()` 返回每个群号的备注。
- `pages/dashboard/index.html`
  - “用量统计”中的 QQ 群号右侧新增铅笔按钮。
  - 新增 `#remarkOverlay` 备注编辑弹窗。
  - 备注以浅灰色 `（备注）` 形式紧跟群号显示。
  - 所有按钮补充 hover 和 active 视觉反馈。
  - 配置 textarea 渲染时把显式 `\n` 转为真实换行。
- `metadata.yaml`
  - 版本号更新为 `0.2.0`。
- `README.md`、`STRUCTURE.md`、`DEVELOP.md`
  - 记录备注、按钮状态和页面配置渲染逻辑。

## 0.1.0 - 2026-06-06

初始版本。

- 新增 `main.py` 插件主体：
  - 注册 `/astrbot_plugin_token_controller/config`、`/astrbot_plugin_token_controller/usage` Web API。
  - 通过 `filter.on_llm_request(priority=1000)` 在 LLM 请求前执行限流判断。
  - 查询 AstrBot 原生 `ProviderStat` 表统计窗口内群聊 token 用量。
  - 支持 `unique_session` 常见 QQ 群 session 形式匹配。
- 新增 `_conf_schema.json`：
  - 定义原生 WebUI 插件配置项。
  - Page 悬浮配置页复用同一份字段含义。
- 新增 `pages/dashboard/index.html`：
  - 左栏展示群 token 用量进度条。
  - 右栏提供悬浮配置页。
  - 使用 `window.AstrBotPluginPage.apiGet/apiPost` 调用插件后端 API。
- 新增 `metadata.yaml`：
  - 声明插件元数据、支持平台和 Plugin Page。
- 新增 `README.md`、`DEVELOP.md`、`STRUCTURE.md` 文档。

## 长期结构约束

- 不维护独立 token 计数数据库，统计来源始终为 AstrBot 原生 `ProviderStat`。
- 不拦截无需调用 LLM 的插件命令或普通消息。
- 配置保存始终写回 AstrBot 注入的插件 `AstrBotConfig` 对象。
- 群备注是插件页面辅助数据，不属于限流配置，独立保存在插件数据目录。
- LLM 请求钩子与 Plugin Page 用量状态必须共用同一套限流状态计算规则。
- 历史统计使用 AstrBot 原生 `ProviderStat` 作为数据源，插件只保存小时级绝对桶值和总量，不维护独立请求明细。
