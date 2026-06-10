</div>

<div align="center">

# astrbot_plugin_token_controller


*_✨ [**AstrBot**](*https://github.com/AstrBotDevs/AstrBot*) 群聊 LLM token 用量控制插件 ✨_*

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
![AstrBot >=4.24.2](https://img.shields.io/badge/AstrBot-4.24.2%2B-green.svg)
![Plugin Page](https://img.shields.io/badge/适配-最新_Plugin_Page-orange.svg)
[![GitHub](https://img.shields.io/badge/作者-青尘工作室-cyan)](https://space.bilibili.com/385556208/)

</div>

用于 AstrBot 的 QQ 群聊 LLM token 流量控制插件。

支持 `群聊级`/`用户级` 的全局 or 独立**流量配额控制**，支持灵活配置**超量策略** (停止 LLM 调用 / 阶梯式切换到低价模型)，支持配置多种预置的 **token 节约 & 缓存命中优化策略**。

支持**可视化查看**当日 token 用量、限流状态、历史统计数据、用户详细用量数据等。

![Plugin Page 主页面](https://raw.githubusercontent.com/QingchenWait/astrbot_plugin_token_controller/refs/heads/main/src/img_mainpage.png)



## 💡 功能

- 🔍 按 QQ 群号设置限流列表，支持为每个群聊单独配置 `备注名` 、 `用量限额` 和 `token 节约策略`。

- ✂️ 支持自定义 token 超限策略：
  - `停止调用 LLM`：群聊原始模型用量达到每日上限后，直接拦截后续 LLM 请求。
  - `回退到其他模型`：群聊原始模型用量达到每日上限后，改用配置的回退模型供应商 (可配置一些低价模型)；回退模型消耗达到“回退模型的用量上限”后，再停止该群所有 LLM 调用。

- 📊 提供 "Plugin Page" 可视化 WebUI 统计和配置页面：
  - 查看每个群聊的实时 token 用量进度和限流状态。
  - 群聊历史 token 用量统计，以 `柱状图` 形式可视化展示统计数据，可自由选择时间跨度。
  - 用户当日 token 用量统计，以 `柱状图` 形式展示群聊内 token 消耗最高的用户排行，支持查看 `每句用户对话` 的精细 token 用量数据。

- 📌 预置了多种 token 节约策略，可针对每个群聊进行个性化配置：
  - `仅通过 @bot 触发 LLM 回复`：开启后，该群的唤醒词触发功能会被静默阻断，仅 `@bot` 才会唤醒 bot。
  - `上下文压缩优化`：控制群聊的最大上下文窗口，并依据窗口值智能裁剪旧历史上下文，节省 token 用量的同时，最大限度保持上下文记忆与回答实时性。
  - `提升缓存命中率`：对于 GPT 和 DeepSeek 模型，支持自动修改请求头，有效提升缓存命中，以减少 token 费用。

## ⚙️ 配置项

#### 基础配置

| 配置项 | 说明 |
| --- | --- |
| `enabled` | 是否启用插件。关闭后不拦截 LLM 请求。 |
| `limited_groups` | 需要限流的 QQ 群号列表。 |
| `daily_token_limit` | 单个群聊每日原始模型 token 上限，单位为 token。 |
| `user_daily_token_limit` | 单个群聊内单个用户每日 token 上限，默认 `-1` 表示不限制。 |
| `refresh_time` | 用量刷新时间，格式 `HH:MM`，按 AstrBot 所在机器本地时区计算。 |
| `qq_platform_names` | 需要识别为 QQ 平台的适配器名称，默认 `aiocqhttp`、`qq_official`、`qq_official_webhook`。 |
| `match_unique_session` | 是否兼容 AstrBot `unique_session` 会话隔离下的历史统计。 |
| `block_message` | 达到停止调用条件后发送到群里的提示文本。 |
| `send_block_message` | 是否发送超限提示；关闭后只静默拦截 LLM 请求。 |

`block_message` 支持变量：`{group_id}`、`{used}`、`{limit}`、`{refresh_time}`、`{window_start}`、`{window_end}`。

#### 用量超限策略配置

| 配置项 | 说明 |
| --- | --- |
| `处理方式` | 用量超限后的措施，可选 `stop_llm` (停止调用 LLM) 或 `fallback_provider` (回退到其他模型)。 |
| `回退的模型供应商` | 回退模型供应商 ID。Plugin Page 可直接选择已加载供应商；原生 WebUI 中需要手动填写供应商 ID。 |
| `回退模型的用量上限` | 原始模型用量超限、切换到回退模型以后，额外还可以再使用的回退模型 token 用量。 |
| `超限后不再响应唤醒词` | 用量超限后，使用唤醒词不会再触发 bot，以节省回退模型的 token，并避免频繁发送超限提示。 |

#### 群聊个性化配置 (仅能在 Plugin Page 中设置)

| 配置项 | 说明 |
| --- | --- |
| `单独配置本群每日用量上限` | 配置后，将会在目标群聊里，使用该值覆盖全局的 `单个群聊每日用量上限`。回退模型用量不变。 |
| `仅通过 @bot 触发 LLM 回复` | 开启后，目标群聊的唤醒词触发功能会被静默阻断，仅 `@bot` 才会唤醒 bot (依旧遵循超限策略的规则)。 |
| `最大上下文窗口设置为额度的 0.5%` | 开启后，目标群聊调用任何 LLM 时，其最大上下文窗口 `max_context_tokens` 会被临时修改。<br /> 修改后，群聊每天具有约 200-300 次的对话额度。 |
| `通过稳定缓存键提升缓存命中率` | `GPT 专用` - 开启后，目标群聊调用 GPT 模型时会为本次请求注入稳定缓存前缀和 `prompt_cache_key`。非 GPT 模型会自动跳过该策略。 |
| `通过稳定前缀提升缓存命中率` | `DeepSeek 专用` - 开启后，目标群聊调用 DeepSeek 模型时会在 `system_prompt` 开头加入稳定缓存锚点，利用 DeepSeek 默认上下文缓存。非 DeepSeek 模型会自动跳过该策略。 |


## 📓 超限策略示例

假设：

- `daily_token_limit = 10000000`
- `over_limit_policy.action = fallback_provider`
- `over_limit_policy.fallback_token_limit = 5000000`

某群当日总用量小于 10M token 时使用原始模型；当日总用量大于等于 10M 且小于 15M token 时，后续 LLM 请求会切换到配置的回退供应商；当日总用量达到 15M token 后，插件会停止该群继续调用任何 LLM 模型。

其他未超限群聊不会被切换，仍使用 AstrBot 默认模型供应商。


## 🔑 使用方式

1. 将本目录作为插件目录放入 AstrBot 的 `data/plugins/astrbot_plugin_token_controller`。
2. 在 AstrBot WebUI 的插件管理中加载或重载插件。
3. 在原生 WebUI 插件配置页或 Plugin Page 的弹窗中填写群号、每日上限、刷新时间和超限策略。
4. 打开 Plugin Page 查看限流群当前窗口内的用量进度，也可以点击“历史 token 用量统计”查看趋势图，或点击“今日用户 token 用量统计”查看单群用户排行。
5. 在用量统计中点击群聊右侧齿轮，可设置该群每日上限，并在“本群 token 节约策略”中勾选“仅通过 @bot 触发 LLM 回复”“最大上下文窗口设置为额度的 0.5%”“通过稳定缓存键提升缓存命中率”或“通过稳定前缀提升缓存命中率”。

从旧目录 `data/plugins/astrbot_plugin_token_limit` 升级时，插件会在新数据目录缺少对应文件时复制旧目录中的备注、群策略、历史统计和用户统计 JSON；不会删除旧目录，也不会覆盖新目录中已有文件。

## 📊 (开发者须知) 统计说明

- 当前用量和历史用量均来自 AstrBot 原生 `ProviderStat`。
- 今日用户用量统计按当前 `refresh_time` 切分的统计周期展示，并在后台持久化保存近 48 小时的用户小时桶、请求归因数据和对话明细；超过 48 小时的数据会被清理，JSON 使用缩进格式便于管理员直接查看。
- 对话明细只保存每次有效请求的时间、用户输入前 20 个字和当次 token 用量，不保存 AstrBot 附加的系统提示词。
- 用户 token 用量只在单个群聊内统计，同一 QQ 用户在不同群里的用量不会合并。
- 对于 AstrBot `ProviderStat.umo` 无法直接解析出用户 QQ 号的群聊记录，插件会使用 LLM 请求事件中记录的用户号、请求时间和会话 ID 进行轻量级归因；无法获取昵称时显示 QQ 号。
- `user_daily_token_limit >= 0` 时启用单用户限流；用户达到上限后，仅该用户在该群里的 LLM 请求会被静默阻断，非 LLM 请求不受影响。Plugin Page 的今日用户柱状图会将超限用户标红。
- 单群上下文窗口节约策略只影响该群本次 LLM 请求期间的临时 provider 副本和本次请求的历史上下文；插件不会写回 AstrBot 供应商全局配置或持久化裁剪后的会话历史。若系统提示词、工具提示或当前输入等必需内容已超过压缩阈值，插件会仅抬高本次临时窗口以减少无意义压缩，同时至少保留最近 3 轮历史。
- Plugin Page 会在单群上下文限制值 `< 15K` 或 `>= 200K` 时显示黄色风险提示，保存中、重置中和错误提示优先显示。
- 稳定缓存键策略只影响该群本次 GPT 请求：插件会合并 `custom_extra_body.prompt_cache_key`，并在 `system_prompt` 开头加入一段稳定缓存锚点，帮助 OpenAI Prompt Caching 满足“相同前缀”条件；不会重排工具 schema、当前输入或历史上下文。OpenAI-compatible 中转是否支持该参数取决于中转实现。
- DeepSeek 稳定前缀策略只影响该群本次 DeepSeek 请求：插件仅在 `system_prompt` 开头加入同版本稳定缓存锚点，不发送 OpenAI 专属缓存参数，不做摘要、不重排历史、不额外调用模型。DeepSeek 官方上下文缓存默认开启，后续请求复用相同前缀时会在响应 usage 中体现 `prompt_cache_hit_tokens`。
- 两个缓存命中策略相互独立，插件会按当前原始或回退 provider 的模型自动识别 GPT / DeepSeek；模型不匹配时即使勾选也会跳过。Plugin Page 在本次打开弹窗后勾选任一缓存策略时，会提示确认未启用会修改 LLM 请求体的其他插件。
