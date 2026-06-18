# dnd-dm-agent

本仓库 fork 自 [NanoBot](https://github.com/HKUDS/nanobot)，一个由 Python 编写的超轻量 OpenClaw。

dnd-dm-agent 将在此基础上进行二次开发，并默认集成 [dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill) 提供的 D&D 地下城主人格、规则与技能。

## 项目来源

- Agent 框架：[HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- D&D DM Soul 与 Skill：[ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill)

## 架构方向

本项目参考仓库 `old_ver` 分支中已经验证的 D&D DM Agent 数据架构，并由 NanoBot 接管通用 Agent 运行时：

```text
QQ / WebUI / CLI
        │
        ▼
NanoBot Runtime
  Provider · Agent Loop · Session · Memory · Channel · Subagent
        │
        ▼
D&D Adapter / Tools
  身份与权限 · 战役作用域 · 状态读写 · 工具调用
        │
        ▼
D&D Domain Core
  战役 · 人物卡 · 规则 · 骰子 · 战斗 · 存档 · 审计
        │
        ▼
SQLite / PostgreSQL
```

当前阶段只迁移数据库系统，尚未迁移旧版模式路由、LLM Loop、API、前端或业务工具。

数据库层位于 `nanobot/dnd/db/`，默认使用 `~/.nanobot/dnd/dnd_dm.db`，也可通过 `DND_DATABASE_URL` 指向其他 SQLite 或 PostgreSQL 数据库。

当前 Schema 按 dnd-dm-skill 的运行文件重构：

| Skill 状态 | 数据表 |
|---|---|
| `world_state.json` | `world_states` |
| `live_party.json` | `parties`、`characters` |
| `combat_state.json` | `combats` |
| `saves/存档*.json` | `campaign_saves` |
| `plot_summary.json` | `plot_summaries` |
| `MODULE_ARC.md`、章节文件 | `module_sources`、`module_chapters` |
| `scenes_index.json`、场景缓存 | `scene_indexes`、`scene_states` |
| SRD 与规则书 | `rule_sources`、`rule_chunks` |
| 骰点与工具执行 | `dice_rolls`、`tool_audits` |
| QQ 等渠道绑定 | `channel_bindings` |

复杂状态采用带 `schema_version` 和 `state_version` 的 JSON 聚合；角色 HP、等级、AC 等高频字段单独成列。数据库升级由内置 Alembic Revision 管理。

沿用 `old_ver` 的数据库核心设计：

- 数据库是战役状态、人物卡、HP、回合额度和审计记录的唯一事实源。
- 骰点、伤害、法术位和动作经济由确定性 Python 工具结算，不交给模型自行计算。
- QQ 用户、角色绑定、当前战役和 DM 权限由适配层解析，不允许模型自行指定。
- NanoBot 的 Session 与 Memory 只负责对话上下文，不替代 D&D 领域状态。
- 不迁移 Lobby、DM、骰娘三模式及其专属状态表。

## NapCat QQ

本仓库内置 NanoBot 的 NapCat Forward WebSocket 渠道，并集成 `napcat-qq` Skill。
QQ 私聊目标使用 `private:<QQ号>`，群聊目标使用 `group:<群号>`；跨会话主动发送时通过
`message` 工具指定 `channel: "napcat"`。详细配置参见 `docs/chat-apps.md`。
