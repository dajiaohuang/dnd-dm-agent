# 🐉 SagaSmith Agent

[English](README.md) | [中文](README-cn.md)

**自主 AI 地下城主运行时** — 基于 [NanoBot](https://github.com/HKUDS/nanobot)，集成完整 D&D 5e DM 能力。

> *"规则书为经文，模组为地图，骰子为审判官。"*  
> — 明萨拉·班瑞，SagaSmith 默认 DM

SagaSmith Agent 是一个完整可运行的 AI DM 系统。接入 QQ（NapCat）、Telegram、WebSocket 等聊天频道，玩家在群里发消息即可跑团。背后是 SQLite/PostgreSQL 战役数据库、BGE-M3 规则检索引擎、d20 战斗计算引擎，以及一个守序邪恶的卓尔 DM 人格。

---

## 生态

| 仓库 | 定位 |
|------|------|
| 🎲 **SagaSmith-agent**（本仓库） | 完整 AI DM 运行时 |
| 📦 [SagaSmith-skill](https://github.com/dajiaohuang/SagaSmith-skill) | 跨平台 skill 插件包（NanoBot / OpenClaw / Hermes） |
| ✍️ [SagaSmith-modulegen](https://github.com/dajiaohuang/SagaSmith-modulegen) | 独立模组生成器（纯 Markdown skill） |

---

## 能力概览

| 系统 | 说明 |
|------|------|
| 🎲 **规则引擎** | BGE-M3 Dense Vector 检索 2700+ SRD 规则块，精确名称 + FTS + 语义混合搜索 |
| ⚔️ **战斗引擎** | d20 真实掷骰、先攻/命中/伤害/豁免/重击计算、回合追踪、XP 结算 |
| 🏛️ **战役数据库** | SQLAlchemy ORM + Alembic 迁移，完整 campaign CRUD、Snapshot 存档/读档/校验/撤销 |
| 📖 **模组管理** | PDF/HTML/DOCX 导入、结构感知分块、场景索引、Dense 检索 |
| 🎭 **明萨拉人格** | 守序邪恶 DM，2024 规则绝对主义，冷刺幽默，绝不泄露隐藏信息 |
| 💬 **多频道接入** | QQ（NapCat OneBot v11）、Telegram、WebSocket、WebUI |

---

## 快速开始

```powershell
# 1. 安装
pip install -e .

# 2. 初始化工作区
nanobot onboard --wizard

# 3. 导入 SRD 规则库
python -m nanobot.dnd.db.cli rules ingest-srd

# 4. 启动 gateway + QQ
.\scripts\start-all.ps1 -Quick
```

WebUI 默认在 `http://127.0.0.1:18765`。

---

## 频道接入

### QQ（NapCat）

QQ 通过 NapCat（OneBot v11 Forward WebSocket）接入，首次运行自动安装：

```powershell
# 一键启动（免扫码 + GPU）
.\scripts\start-quick.bat

# 或：完整启动
.\scripts\start-all.ps1
```

**配置** `~/.nanobot/config.json`：

```json
{
  "channels": {
    "napcat": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "allowFrom": ["<QQ号>"],
      "groupPolicy": "mention",
      "groupPolicyOverrides": {"<群号>": "mention"}
    }
  }
}
```

### Telegram

```json
{
  "channels": {
    "telegram": { "enabled": true, "token": "<bot-token>" }
  }
}
```

### 格式策略

| 频道 | 格式 |
|------|------|
| QQ (NapCat) | 纯文本 + emoji + `【】` 强调，禁用 markdown bold/italic |
| Telegram | 短段落，少量 `**bold**` |
| WebUI / CLI | Markdown / 纯文本 |

---

## 模组导入

支持 Markdown、PDF、DOCX、PPTX、XLSX 等格式。PDF 使用专有结构解析器（分页感知 + 书签恢复 + CJK 重排 + 目录过滤）：

```powershell
python -m nanobot.dnd.db.cli module import --campaign <id> --path "<模组目录>" --name "模组名"
```

分块策略：1200 字上限、≈100 字重叠、不跨标题边界、保留页码。

---

## 规则检索

2700+ SRD 规则块，`BAAI/bge-m3`（1024 维）语义索引：

```powershell
python -m nanobot.dnd.db.cli rules status
python -m nanobot.dnd.db.cli rules search --campaign <id> --query "擒抱逃脱" --top-k 5
```

GPU 加速：`$env:DND_EMBEDDING_DEVICE="cuda"`。

---

## 战役管理

数据库为唯一权威源。Snapshot 存档覆盖战役元数据、世界、队伍、角色、战斗、剧情摘要、事件日志——模组原文不重复嵌套。

```powershell
python -m nanobot.dnd.db.cli campaign create --name "博德之门" --module "BGDIA"
python -m nanobot.dnd.db.cli save create --campaign <id> --label "初始状态"
python -m nanobot.dnd.db.cli save list --campaign <id>
python -m nanobot.dnd.db.cli save load --campaign <id> --slot <n>
```

---

## 架构

```
QQ / Telegram / WebUI
        │
        ▼
NanoBot Runtime  (Provider · Agent Loop · Session · Memory · Channels)
        │
        ▼
D&D Adapter       (dnd_rules 检索 · dnd-engine 机械计算 · 战役数据库)
        │
        ▼
SQLite / PostgreSQL  (规则索引 · 战役状态 · 审计 · Snapshot)
```

### 项目结构

```
SagaSmith-agent/
├── nanobot/                   # Agent 运行时
│   ├── agent/                 #   Agent Loop · Context · Memory · Runner
│   ├── channels/              #   napcat · telegram · websocket
│   ├── dnd/                   #   D&D 适配层 (rules · db · engine · modules)
│   ├── skills/                #   dnd-dm · dnd-campaign-manager · napcat-qq
│   └── templates/             #   系统提示模板 (identity · SOUL · platform_policy)
├── tools/napcat/              # NapCat + 便携 QQ 运行时
├── scripts/                   # 安装与启动脚本
│   ├── setup-napcat.ps1       #   一键安装 NapCat
│   └── start-all.ps1          #   一键启动
└── tests/                     # 测试
```

---

## 上下文管理

| 机制 | 说明 |
|------|------|
| Session JSONL | 实时对话记录 |
| Auto-Compact | token 预算达 30% 时触发压缩 |
| Dream（每 2h） | 长期记忆总结写入 MEMORY.md |

---

## 致谢

- [ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill) — D&D DM skill 先驱，SagaSmith 的灵感与设计参考
- [NanoBot](https://github.com/HKUDS/nanobot) — 轻量 Agent 框架
- D&D 5e SRD 5.2.1 © Wizards of the Coast（[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)）

---

## 许可证

MIT
