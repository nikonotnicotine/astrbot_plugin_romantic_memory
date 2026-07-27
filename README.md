# 恋爱记忆回溯

一个仅处理私聊的 AstrBot 长期记忆插件。插件会把短期对话交给 AstrBot 当前配置的 LLM Provider 总结，再使用 Embedding Provider 转换为向量，保存到本地 ChromaDB。后续私聊中，插件会根据语义相似度、关键词命中和记忆时间衰减，选择相关回忆注入当前上下文。

当前版本：<code>v2.14</code>

## 适用场景

- 让角色记住私聊中的重要事件、约定、偏好和设定；
- 让不同用户的记忆互相隔离；
- 让不同 AstrBot 人格拥有不同的记忆空间；
- 使用本地 ChromaDB，不额外部署向量数据库；
- 通过 Web 面板查看、编辑、导入、导出和删除长期记忆。

插件只处理私聊，不会把群聊内容写入本插件的长期记忆。

## 功能概览

- 私聊会话隔离：默认使用 AstrBot 的 unified_msg_origin 作为会话标识；
- 人格隔离：长期记忆保存 personality_id，检索时同时匹配会话和人格；
- 自动总结：支持按对话轮数自动总结；
- 闲置总结：可以在私聊一段时间没有新消息后自动总结，默认关闭；
- 手动总结：使用 /恋爱记忆，或 /romantic_memory_save，立即整理当前私聊短期对话；
- 工具记忆模式：允许 LLM 主动调用 romantic_memory_save 工具保存记忆；
- 混合检索：语义相似度占 70%，关键词命中占 30%，可叠加时间衰减；
- 多种上下文注入方式：支持用户消息、系统提示词或独立系统消息；
- 本地持久化：使用 ChromaDB 保存文本、元数据和向量；
- Web 管理页面：支持查看、新增、修改、批量删除、清空当前列表、导入、导出和备份；
- 失败保护：总结、Embedding 或存储失败时，短期缓存不会被清除。

## 环境要求

- AstrBot >=4.0.0；
- Python 3.10 或更高版本；
- 至少一个可用的 LLM Provider，用于生成记忆总结；
- 至少一个可用的 Embedding Provider，用于向量化和语义检索；
- 插件依赖：chromadb、gradio、httpx。

如果只希望手动整理文本，也仍然需要可用的 LLM Provider 和 Embedding Provider，因为手动指令同样会先总结，再生成向量。

## 安装与更新

将插件目录放入 AstrBot 的插件目录：

~~~text
data/plugins/astrbot_plugin_romantic_memory
~~~

阈值判定使用未衰减的原始相关性分数；时间衰减只影响排序，不会让原本达到相关性阈值的旧记忆直接失去召回资格。

在 AstrBot 项目根目录安装插件依赖：

~~~powershell
& .\venv\Scripts\python.exe -m pip install -r data\plugins\astrbot_plugin_romantic_memory\requirements.txt
~~~

安装或更新后，请重载插件或重启 AstrBot。

更新插件代码不会自动删除长期记忆数据。更新前建议在面板的 TRANSFER 页面点击 BACKUP ZIP 进行备份。

## 快速开始

### 1. 确认 Provider

在 AstrBot 的 Provider 配置中确认：

- 当前会话有可用的 LLM Provider；
- 系统中至少有一个可用的 Embedding Provider。

插件配置留空时的选择规则：

- LLM：使用当前会话正在使用的 Provider；
- Embedding：使用 AstrBot 找到的第一个可用 Embedding Provider。

### 2. 选择总结模式

默认按轮数自动总结：

~~~text
use_tool_memory = false
enable_idle_summary = false
trigger_rounds = 15
~~~

这表示累计达到 15 轮一问一答后，插件会自动整理当前私聊的短期缓存。

### 3. 开始私聊

直接与机器人私聊即可。插件会在每次 LLM 请求前尝试唤醒相关长期记忆，并在符合条件时自动总结新的短期对话。

### 4. 手动立即整理

在私聊中发送：

~~~text
/恋爱记忆
~~~

别名：

~~~text
/romantic_memory_save
~~~

手动指令会读取当前私聊尚未整理的短期对话，调用总结 LLM，生成向量并保存到 ChromaDB。成功后会清空已保存的短期消息，将轮数归零，并把闲置计时器重置为当前时间。

手动指令在普通模式和工具记忆模式下都可使用，不受 use_tool_memory 开关限制。没有可保存的短期对话，或总结失败时，原短期缓存会保留。

## 自动总结模式

### 普通模式

普通模式由以下配置控制：

~~~text
use_tool_memory = false
trigger_rounds = 15
enable_idle_summary = false
trigger_idle_minutes = 60
~~~

- 达到 trigger_rounds 后触发按轮数总结；
- enable_idle_summary 开启后，私聊闲置达到 trigger_idle_minutes 才触发闲置总结；
- 两种自动总结可以同时开启；
- 默认只开启按轮数阈值，闲置总结默认关闭；
- 自动总结成功后，已保存的短期消息会被移除，轮数会重新计算；
- 自动总结失败时，短期消息不会丢失，可稍后重试或使用 /恋爱记忆。

### 工具记忆模式

设置 use_tool_memory = true 后：

- 不再按 trigger_rounds 自动总结；
- 不再执行 enable_idle_summary 闲置总结；
- LLM 可以主动调用工具 romantic_memory_save；
- 用户仍可以手动发送 /恋爱记忆；
- 工具或总结失败时，短期缓存会保留。

如果模型很少主动保存记忆，可以关闭工具记忆模式，改用按轮数自动总结，或者直接使用 /恋爱记忆。

## 配置说明

所有配置都在 AstrBot 的插件配置页中修改。修改后请重载插件或重启 AstrBot。

### Provider 与总结

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| llm_provider | 空 | 生成长期记忆摘要的 LLM Provider。留空时使用当前会话正在使用的 Provider。 |
| embedding_provider | 空 | 生成向量的 Embedding Provider。留空时使用第一个可用的 Embedding Provider。 |
| include_current_personality | false | 总结时是否把当前系统提示词/人格设定提供给总结 LLM。 |
| summary_prompt | 内置提示词 | 记忆总结提示词。建议要求只输出总结结果，不输出解释性前缀。 |
| include_current_time | true | 是否把当前系统时间提供给总结 LLM，帮助理解相对日期。 |

### 总结触发

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| trigger_rounds | 15 | 普通模式下累计多少轮一问一答后自动总结，最小值为 1。 |
| use_tool_memory | false | 开启 LLM 自动记忆工具。开启后关闭按轮数和闲置自动总结，但不影响手动指令。 |
| trigger_idle_minutes | 60 | 私聊闲置多少分钟后触发闲置总结，最小值为 1。 |
| enable_idle_summary | false | 是否启用闲置自动总结。关闭时不会启动闲置总结后台任务。 |

### 数据存储与隔离

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| collection_name | romantic_memory | ChromaDB 集合名称。修改后会使用新集合，不会自动迁移旧数据。 |
| vector_db_path | 空 | ChromaDB 数据目录。留空时使用 data/plugin_data/astrbot_plugin_romantic_memory/chroma。 |
| enable_isolation | true | 是否按私聊会话隔离短期和长期记忆。关闭后所有私聊共用一个会话空间，一般不建议关闭。 |

### custom_filter_terms

`custom_filter_terms` 用于过滤用户消息开头由 AstrBot 或其他组件附加的元数据，避免时间、天气、地点等动态信息被保存为短期记忆，或参与后续的 Embedding 和关键词召回。

默认会自动过滤以下类型的系统元数据：

```text
[User发送当地时间 ...]$
```

如果其他组件还会在消息开头添加类似的元数据，可以在 `custom_filter_terms` 中填写对应的标记词。例如：

```text
当前天气,当前地点,环境信息
```

也支持使用中文逗号、英文分号、中文分号或换行分隔：

```text
当前天气
当前地点
环境信息
```

插件会忽略空白项和重复项，并且不区分大小写。

过滤只针对消息开头的元数据块，例如：

```text
[当前天气：晴朗，温度 25℃]$
今天感觉很舒服。
```

配置了 `当前天气` 后，最终只会保留：

```text
今天感觉很舒服。
```

用户正文中间或结尾正常出现的相同词语不会被删除。例如：

```text
我喜欢晴朗的天气。
```

这句话仍会正常保存和参与记忆检索。

该配置只会移除消息开头的元数据，不会修改 AstrBot 原始聊天记录。

默认数据目录：

~~~text
data/plugin_data/astrbot_plugin_romantic_memory/chroma
~~~

不要手动修改 chroma 目录中的文件。迁移或备份时，优先使用面板的 ZIP 备份和导出功能。

### 记忆唤醒与上下文注入

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| recall_top_k | 5 | 每次最多选择多少条候选记忆，范围为 1 到 50。 |
| context_keep_limit | 5 | 第一通道直接注入当前会话/人格最近 N×24 小时内的全部记忆；0 表示不匹配任何过去时间，-1 表示注入全部记忆。该通道不受相关性、数量和字符数配置限制。 |
| insert_method | user_prompt | 注入位置：user_prompt、system_prompt 或 insert_system_prompt。 |
| insert_position | prepend | 放在目标内容前面还是后面：prepend 或 append。 |
| max_input_length | 4000 | 发送给 Embedding Provider 前的最大字符数，超出部分会截断。 |
| max_inject_chars | 6000 | 单次最多注入的记忆字符数。0 表示不注入，但不影响保存。 |
| recall_score_threshold | 0.35 | 最低唤醒分数。调高更谨慎，调低更容易唤醒。 |
| enable_time_decay | true | 是否启用时间衰减。关闭后只按语义和关键词相关性排序。 |
| time_decay_coefficient | 0.01 | 每经过一天扣除的分数，值越大旧记忆越不容易唤醒。 |
| recall_system_prompt | 内置模板 | 记忆注入模板，必须保留 {memory_text} 占位符。 |

双通道行为说明：用户发送消息时，插件会先直接注入近期记忆；同时保留原有语义召回，根据当前消息召回相关长期记忆。`recall_top_k`、`max_inject_chars`、`recall_score_threshold` 和时间衰减只影响语义召回通道，已由近期通道注入的记忆不会重复注入。

最终相关性大致为：

~~~text
语义分数 × 0.7 + 关键词分数 × 0.3 - 记忆年龄天数 × time_decay_coefficient
~~~

如果没有记忆被唤醒，依次检查 recall_score_threshold、context_keep_limit、时间衰减、session_id、personality_id 和 max_inject_chars。

### Short-term archive

未总结的私聊记录会即时保存到插件数据目录 `short_term/<personality_id>/<session_id>.json`，AstrBot 重启后会自动恢复，因此总结使用持久化记录而不是仅存在内存中的缓存。面板的 `SHORT-TERM` 页面可以查看当前会话，编辑 `content`，或删除单条记录；`role` 和 `type` 只读。总结输入只保留 user/assistant 的可见文本，不会发送 thinking 思考链。

## Web 管理面板

重载插件或重启 AstrBot 后，在 Web 控制台插件详情中进入“恋爱记忆回溯”的 panel 页面。

## 运行日志与故障排查

Romantic Memory 会将运行状态写入 AstrBot 日志，日志前缀为：

~~~text
[Romantic Memory]
~~~

### 日志内容

日志通常包括：

- 插件加载和 ChromaDB 连接状态；
- 记忆召回命中、未命中和跳过原因；
- 实际注入的记忆内容；
- 自动总结和手动总结结果；
- Embedding Provider 不可用；
- ChromaDB、Web API、总结和召回异常。

召回或总结发生异常时，应优先查看 AstrBot 日志中的 [Romantic Memory] 记录。成功注入时，日志会打印本次实际注入的记忆正文，方便确认人格、会话和检索结果是否正确。

> 注意：由于日志可能包含记忆正文，请注意日志文件的隐私性。

### OVERVIEW

查看 ChromaDB 状态、Embedding Provider 状态、短期会话数量、短期缓存数量、当前人格、当前会话和最近一次记忆召回结果。

Overview 面板属于实验性功能，具体运行情况和错误信息请查看 AstrBot 日志。

### MEMORIES

支持：

- 按 SESSION、PERSONA ID 和关键词筛选；
- 点击 EDIT 修改内容、日期、会话和人格；
- 勾选多条后点击 DELETE SELECTED；
- 点击 CLEAR CURRENT 删除当前筛选结果；
- 删除前进行两步页面内确认；
- 删除成功后自动刷新列表。

页面删除只影响长期记忆，不会清除 AstrBot 聊天记录。

### SHORT-TERM

输入人格 ID，即可查看该人格下所有尚未总结的短期消息。每张卡片仍显示所属会话，编辑或删除时由页面自动带上会话标识。

### TRANSFER

支持导入 TXT、Markdown、JSON，导出 JSON、Markdown、TXT，以及 ZIP 备份。文件导入完成后会显示“上传成功，已导入 N 条记忆”的提示。

页面只管理记忆和监控，不修改插件配置。

### 召回行为说明

context_keep_limit 控制第一路注入：用户发消息时，直接注入当前会话和人格下最近 N 天内的全部记忆；-1 表示不限制时间。该路不使用相关性、数量或字符数限制。

同时保留第二路语义召回：根据用户当前消息使用 Embedding、相关性阈值、时间衰减、recall_top_k 和 max_inject_chars 召回相关长期记忆。已经通过第一路注入的记忆不会在第二路重复注入。

## 会话 ID 与人格 ID

### 会话 ID

session_id 通常来自 AstrBot 的 UMO。使用 /sid 查看当前会话，例如：

~~~text
UMO: 「default:FriendMessage:YOUR_USER_ID」
~~~

在面板中填写：

~~~text
default:FriendMessage:YOUR_USER_ID
~~~

不要填写前面的 UMO: 和中文引号。

### 人格 ID

personality_id 是 AstrBot 当前使用的人格标识。开启会话隔离和人格隔离时，session_id 与 personality_id 都匹配，记忆才会在当前对话中被检索。

如果面板能看到记忆，但聊天时没有唤醒，优先检查当前私聊会话、当前人格、面板中的 PERSONA ID，以及导入时填写的会话和人格。

## 导入格式

### TXT / Markdown

插件按物理行读取 TXT 和 Markdown，每一行是一条记忆。推荐格式：

~~~text
# 核心记忆归档
# 格式：YYYY-MM-DD | 发生的一件事
2026-02-03 | 用户与角色讨论并确认了人格与世界观设定。
2026-02-04 | 双方确认了称呼偏好，并约定将某个地点作为共同记忆场所。
2026-02-04 | 对话中形成了一条会影响后续互动的长期规则。
~~~

规则：

- 日期使用 YYYY-MM-DD；
- 同一天可以有多条记忆；
- 一行可以包含多个句子，但不要在一条记忆中手动换行；
- # 开头的标题和说明会被忽略；
- - 内容 和 * 内容 开头的 Markdown 列表符会被自动去掉；
- 不要使用 YAML 的 date:、content: 片段格式；
- 长篇历史建议拆成多个相对独立的事件，便于检索。

导入页面中的 PERSONA ID 和 SESSION 会作为默认值。文件中的记录缺少对应字段时，会使用页面填写的默认值。

### JSON

JSON 可以携带完整的会话、人格、日期和时间戳：

~~~json
{
  "memories": [
    {
      "session_id": "default:FriendMessage:YOUR_USER_ID",
      "personality_id": "your-personality-id",
      "date": "2026-02-03",
      "content": "用户与角色确认了重要的人格与世界观设定。",
      "timestamp": 1770076800
    }
  ]
}
~~~

也可以导入单条对象：

~~~json
{
  "content": "用户喜欢在睡前听一段简短的晚安故事。",
  "session_id": "default:FriendMessage:YOUR_USER_ID",
  "personality_id": "default",
  "date": "2026-02-03"
}
~~~

content 不能为空。缺少 session_id、personality_id 或 date 时，会使用页面默认值或当前时间生成日期。

## 总结提示词建议

自动总结和 /恋爱记忆 都使用 summary_prompt。建议：

- 只输出总结内容，不输出解释性前缀；
- 记录事实、偏好、约定、重要事件和长期规则；
- 不要把临时寒暄、重复内容和无关细节写入长期记忆；
- 对角色设定和称呼偏好使用稳定、明确的表述；
- 一次总结输出一段相对独立的记忆。

示例：

~~~text
请以客观第三人称视角记录，使用单段连贯的自然语言表述，避免重复。省略修饰词，禁止使用颜文字和括号描述动作。仅客观记录核心事件、关键设定、冲突及解决方案，禁止添加评价和情感感悟。禁止极端用词。若包含特定人设或规则请精准保留。只需输出总结结果，不要输出解释性前缀。
~~~

## 常见问题

### 面板没有 panel 页面

重启 AstrBot，让 Dashboard 重新发现：

~~~text
data/plugins/astrbot_plugin_romantic_memory/pages/panel/index.html
~~~

### 面板显示 ChromaDB OFFLINE

检查插件依赖、vector_db_path 权限、collection_name 格式，以及 AstrBot 日志中的 ChromaDB 初始化错误。

### 面板显示 Embedding OFFLINE

检查 AstrBot 是否配置了 Embedding Provider，也可以在插件配置中明确填写 embedding_provider。

### 记忆总结失败

检查 LLM Provider、Embedding Provider、summary_prompt，以及当前私聊是否有尚未总结的短期对话。总结失败时，短期缓存会保留。

### 保存后没有唤醒

检查 session_id、personality_id、recall_score_threshold、context_keep_limit 和时间衰减配置。保存成功和被当前对话唤醒是两个独立步骤。

### 修改配置后没有变化

重载插件或重启 AstrBot。修改 collection_name 后不会自动迁移旧集合，旧记忆仍保存在原集合中。

## 作者与反馈

作者：**nikonotnicotine**

GitHub：<https://github.com/nikonotnicotine/astrbot_plugin_romantic_memory>

## 致谢

感谢 Mnemosyne 插件作者 **lxfight** 提供的长期记忆插件灵感：

<https://github.com/lxfight/astrbot_plugin_mnemosyne>

本插件与 Mnemosyne 的数据和运行逻辑相互独立，不迁移、不读取 Mnemosyne 数据，也不要求安装 Mnemosyne 才能运行。本插件使用 ChromaDB，并独立实现自己的会话隔离、人格隔离和检索流程。

## v2.12 Release Notes

- Pending short-term conversations are persisted by personality and restored after AstrBot restarts.
- The SHORT-TERM panel lists all pending records by PERSONA ID; each card keeps its session identity for edit/delete operations.
- The automatically injected time/weather header is kept in the normal conversation context so the LLM can perceive it, but it is removed from short-term summary data and from semantic/keyword recall queries.
- Time, weather, city, and temperature mentioned by the user as part of the actual message remain valid recall content and continue to participate in embedding, keyword matching, and ranking.

## v2.13 Release Notes

- Added custom_filter_terms for multiple custom automatic metadata markers.
- Custom filter terms can be separated by commas, Chinese commas, semicolons, Chinese semicolons, or newlines.
- The built-in [User发送当地时间 ...]$ metadata header remains filtered by default.
- Filtering applies only to a detected leading metadata block; matching words in the user’s actual message remain available for recall and ranking.

## v2.14 Release Notes

- Added the standalone Love Memory home page for the plugin panel.
- Added configurable names, start date, avatars, and signatures for the Love Memory page.
- Love Memory profile settings are now persisted by the plugin and survive page reloads and re-entering the panel.
- Fixed Love Memory date calculations, signature rendering, and page resource cache invalidation.