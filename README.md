# dnd-dm-agent

本仓库 fork 自 [NanoBot](https://github.com/HKUDS/nanobot)，一个由 Python 编写的超轻量 OpenClaw。

dnd-dm-agent 将在此基础上进行二次开发，并默认集成 [dnd-dm-skill](https://github.com/dajiaohuang/dnd-dm-skill) 提供的 D&D 地下城主人格、规则与技能。

## 项目来源

- Agent 框架：[HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- D&D DM Soul 与 Skill：[dajiaohuang/dnd-dm-skill](https://github.com/dajiaohuang/dnd-dm-skill)

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
  身份与权限 · 战役作用域 · 模式路由 · 工具调用
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

沿用 `old_ver` 的数据库核心设计：

- 数据库是战役状态、人物卡、HP、回合额度和审计记录的唯一事实源。
- 骰点、伤害、法术位和动作经济由确定性 Python 工具结算，不交给模型自行计算。
- QQ 用户、角色绑定、当前战役和 DM 权限由适配层解析，不允许模型自行指定。
- NanoBot 的 Session 与 Memory 只负责对话上下文，不替代 D&D 领域状态。
- 不迁移 Lobby、DM、骰娘三模式及其专属状态表。
