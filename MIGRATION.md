# D&D DM Agent — Handover

将本项目的所有自定义内容打包为一个独立 plugin，安装到任意 NanoBot / OpenClaw / Hermes 实例。

## 最小结构

```
dnd-dm-plugin/
│
├── skills/                          # 3 个 Skill 目录（纯 Markdown，跨平台）
│   ├── dnd-dm/
│   │   ├── SKILL.md                 # 核心 DM 人格，always: true
│   │   ├── references/
│   │   │   ├── DM_RULES.md          # 裁判规则（数据权威、存档、场景、战斗、输出模板）
│   │   │   ├── DM_TEMPLATES.md      # 输出格式模板
│   │   │   ├── CHAR_CREATION.md     # 角色创建流程
│   │   │   ├── MODULE_INDEX.md      # 模组索引/场景导航
│   │   │   └── MODULE_ARC.md        # 模组弧线/章节管理
│   │   └── srd/
│   │       ├── SKILL.md             # SRD 使用说明
│   │       └── references/
│   │           └── DND5eSRD_*.md    # × 20，SRD 5.2.1 CC-BY-4.0
│   ├── dnd-campaign-manager/
│   │   ├── SKILL.md                 # 战役生命周期管理（开团、存档、模组导入）
│   │   └── references/
│   │       └── database-contract.md # 数据库结构约定
│   └── dnd-module-gen/
│       └── SKILL.md                 # 模组生成（one-shot/short/medium/long/sandbox + 25 范式）
│
├── templates/                       # SOUL 模板（纯 Markdown，跨平台）
│   ├── SOUL.md                      # 明萨拉·班瑞 DM 人格（守序邪恶、规则严苛）
│   ├── IDENTITY.md                  # 身份约束（2024 规则、章节锁、里程碑等级）
│   ├── AGENTS.md                    # 会话启动协议、记忆管理
│   ├── agent/
│   │   └── identity.md              # 运行时注入的 identity 提示
│   └── memory/
│       └── MEMORY.md                # 长期记忆模板（预置 D&D 分区）
│
├── tools/                           # Agent 工具（平台相关）
│   ├── dnd_campaign.py              # 战役 CRUD + 一键开团
│   ├── dnd_save.py                  # 存档管理（create/list/verify/restore/delete/export）
│   ├── dnd_module.py                # 模组管理（import/search/set_scene/current）
│   └── dnd_rules.py                 # 规则检索（search/expand/status）
│
├── domain/                          # 业务逻辑（纯 Python，无框架依赖）
│   ├── db/                          # 数据库层
│   │   ├── database.py              # SQLAlchemy Database 类
│   │   ├── campaigns.py             # CampaignService
│   │   ├── characters.py            # CharacterService
│   │   ├── events.py                # CampaignEventService
│   │   ├── snapshots.py             # CampaignSnapshotService
│   │   ├── module_content.py        # ModuleImportService
│   │   ├── module_progress.py       # ModuleProgressService
│   │   ├── undo.py                  # UndoManager
│   │   ├── user_context.py          # USER.md player-role 同步
│   │   ├── cli.py                   # JSON CLI
│   │   ├── models/                  # 18 个 ORM 模型（7 文件）
│   │   └── migrations/              # 6 个 Alembic 迁移脚本
│   ├── modules/                     # 模组处理
│   │   ├── chunking.py              # 结构感知分块
│   │   ├── pdf_parser.py            # PDF→Markdown
│   │   ├── scene_utils.py           # 场景解析工具
│   │   └── search.py                # ModuleSearchService
│   ├── rules/                       # 规则引擎
│   │   ├── embedding.py             # BgeM3Embedder
│   │   ├── parser.py                # Markdown 解析
│   │   ├── ingest.py                # RuleIngestService
│   │   └── search.py                # RuleSearchService
│   └── engine/                      # 机制计算
│       ├── dice.py                  # d20/任意骰
│       ├── checks.py                # 技能/豁免/攻击检定
│       ├── resolve.py               # 命中/伤害/DC 计算
│       ├── xp.py                    # XP 计算
│       └── templates.py             # 角色/怪物模板
│
├── data/                            # 首次安装时需要的数据
│   ├── srd/                         # SRD 5.2.1 英文 (20 文件)
│   └── srd-zh/                      # SRD 中文翻译（可选子模块）
│
└── README.md                        # 安装说明
```

## 文件清单

| 目录 | 文件数 | 说明 |
|------|--------|------|
| skills/ | 29 | 3 × SKILL.md + 5 × references + 20 × SRD + 1 × SRD SKILL.md |
| templates/ | 6 | SOUL + IDENTITY + AGENTS + identity.md + MEMORY.md |
| tools/ | 4 | Python Tool 类（NanoBot 框架） |
| domain/ | ~40 | 纯 Python，无框架依赖 |
| data/ | ~22 | SRD 原文，首次安装时 ingest |

## 各平台安装

### NanoBot

```bash
cp -r skills/*     ~/.nanobot/skills/
cp -r templates/*  ~/.nanobot/templates/
cp -r tools/*.py   ~/.nanobot/agent/tools/
cp -r domain/*     ~/.nanobot/dnd/
cp -r data/srd     ~/.nanobot/dnd/data/srd/

# 首次导入 SRD
python -m nanobot.dnd.db.cli rules ingest-srd
```

### OpenClaw

`skills/` + `templates/` + `data/` 直接复制。`tools/` 通过 TypeScript `api.registerTool()` 包装后再调 Python domain 层（subprocess JSON 桥）。

### Hermes

`skills/` 通过 `ctx.register_skill()`；`templates/` 通过 `ctx.inject_message()`；`tools/` 通过 `ctx.register_tool()` 包装 domain 层。

## 不纳入 Plugin 的部分

| 组件 | 原因 |
|------|------|
| `nanobot/agent/loop.py` 等框架代码 | 属于 NanoBot 本体 |
| `nanobot/providers/`, `session/`, `config/` | 同上 |
| `nanobot/channels/` | 除 napcat 外均为上游 |
| Engine 死代码（6 个 DEPRECATED） | 已被 DB 取代 |
| `scripts/start-*.bat` | 环境相关 |
| `tools/NapCat.Shell/` | QQ 运行时，单独安装 |
| `references/nanobotREADME.md`、`docs/`、`webui/` | 上游文档 |
