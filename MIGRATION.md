# D&D DM Agent — Plugin 打包计划

将当前 `dnd-dm-agent` 的所有自定义内容打包为一个 NanoBot plugin，
使其能一键安装到任意 vanilla NanoBot 实例。

## 一、Plugin 结构

```
dnd-dm-plugin/
├── pyproject.toml              # pip install dnd-dm-plugin
├── README.md
├── setup.py / hatch_build.py
│
├── dnd_dm_plugin/
│   ├── __init__.py             # plugin entry: register tools, skills, templates
│   │
│   ├── db/                     # 数据库层 (从 nanobot/dnd/db/ 提取)
│   │   ├── database.py         # Database 类，会话工厂，Alembic 迁移
│   │   ├── campaigns.py        # CampaignService
│   │   ├── characters.py       # CharacterService
│   │   ├── events.py           # CampaignEventService
│   │   ├── snapshots.py        # CampaignSnapshotService
│   │   ├── module_content.py   # ModuleImportService
│   │   ├── module_progress.py  # ModuleProgressService
│   │   ├── undo.py             # UndoManager
│   │   ├── cli.py              # JSON CLI (保留，工具调用的后备)
│   │   ├── models/             # 18 个 ORM 模型
│   │   │   ├── __init__.py
│   │   │   ├── common.py
│   │   │   ├── campaign.py
│   │   │   ├── knowledge.py
│   │   │   ├── module.py
│   │   │   ├── runtime.py
│   │   │   ├── audit.py
│   │   │   └── integration.py
│   │   └── migrations/         # 6 个 Alembic 迁移脚本
│   │
│   ├── modules/                # 模组处理 (从 nanobot/dnd/modules/)
│   │   ├── chunking.py         # 分块
│   │   ├── pdf_parser.py       # PDF 解析
│   │   ├── scene_utils.py      # 场景解析工具
│   │   └── search.py           # ModuleSearchService
│   │
│   ├── rules/                  # 规则引擎 (从 nanobot/dnd/rules/)
│   │   ├── embedding.py        # BgeM3Embedder
│   │   ├── parser.py           # Markdown 解析
│   │   ├── ingest.py           # RuleIngestService
│   │   └── search.py           # RuleSearchService
│   │
│   ├── engine/                 # 骰子与战斗计算 (从 dnd-engine 提取)
│   │   ├── dice.py             # roll_d20, roll_dice
│   │   ├── checks.py           # skill/save/attack 检定
│   │   ├── resolve.py          # 伤害/DC 计算
│   │   ├── xp.py               # 经验值计算
│   │   └── templates.py        # 角色/怪物模板
│   │
│   └── tools/                  # Agent 工具 (从 nanobot/agent/tools/ 提取)
│       ├── dnd_campaign.py     # DndCampaignTool
│       ├── dnd_module.py       # DndModuleTool
│       ├── dnd_rules.py        # DndRulesTool
│       └── dnd_save.py         # DndSaveTool
│
├── skills/                     # Skill 定义
│   ├── dnd-dm/                 # DM 人格 Skill
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── DM_RULES.md
│   │       ├── DM_TEMPLATES.md
│   │       ├── CHAR_CREATION.md
│   │       ├── MODULE_INDEX.md
│   │       └── MODULE_ARC.md
│   ├── dnd-campaign-manager/   # 战役管理 Skill
│   │   └── SKILL.md
│   ├── dnd-module-gen/         # 模组生成 Skill
│   │   └── SKILL.md
│   └── napcat-qq/              # QQ 频道 Skill
│       └── SKILL.md
│
├── templates/                  # SOUL 模板
│   ├── SOUL.md                 # 明萨拉·班瑞 DM 人格
│   ├── IDENTITY.md             # 身份约束
│   ├── AGENTS.md               # 会话启动协议
│   ├── agent/
│   │   └── identity.md         # 运行时注入模板
│   └── memory/
│       └── MEMORY.md           # 长期记忆模板
│
├── data/                       # 规则数据
│   ├── srd/                    # SRD 5.2.1 英文 (20 文件)
│   │   └── DND5eSRD_*.md
│   └── srd-zh/                 # SRD 中文翻译 (子模块)
│
├── channels/                   # 频道 (可选)
│   └── napcat.py               # NapCat QQ 频道
│
└── scripts/                    # 启动与管理脚本
    ├── plugin_install.py       # 一键安装：pip install + 导入 SRD
    ├── plugin_status.py        # 状态检查
    └── plugin_update.py        # 升级迁移
```

## 二、依赖

```
pyproject.toml [project.dependencies]:
  sqlalchemy >=2.0
  alembic >=1.13
  pypdf >=4.0
  sentence-transformers >=3.0
  numpy >=1.26
  markitdown >=0.0.1

[project.optional-dependencies]:
  gpu = ["torch >=2.0"]
```

不需要依赖 nanobot 本身——工具使用框架的 Tool/ToolContext 基类，安装时动态适配。

## 三、安装流程

```bash
# 1. Install plugin
pip install dnd-dm-plugin

# 2. Install to NanoBot instance
python -m dnd_dm_plugin install --target ~/.nanobot/

# This copies:
#   tools/*.py          → ~/.nanobot/agent/tools/dnd_*.py
#   skills/*/           → ~/.nanobot/skills/
#   templates/*         → ~/.nanobot/templates/
#   channels/napcat.py  → ~/.nanobot/channels/napcat.py

# 3. Import SRD (one-time)
python -m dnd_dm_plugin ingest-srd --lang en
python -m dnd_dm_plugin ingest-srd --lang zh-CN

# 4. Verify
python -m dnd_dm_plugin status
# → DB: OK (dnd_dm.db, schema v6)
# → SRD: 2700+ chunks indexed
# → Tools: dnd_campaign, dnd_module, dnd_rules, dnd_save
# → Skills: dnd-dm, dnd-campaign-manager, dnd-module-gen, napcat-qq
# → SOUL: Minthara Baenre DM persona
```

## 四、适配层

Plugin 不硬编码 NanoBot 路径。通过一个适配层解决框架差异：

```python
# dnd_dm_plugin/adapters.py

class NanoBotAdapter:
    """Default adapter for vanilla NanoBot."""
    tool_base = "nanobot.agent.tools.base:Tool"
    skill_path = "~/.nanobot/skills"
    template_path = "~/.nanobot/templates"
    channel_path = "nanobot.channels"

class OpenClawAdapter:
    """Adapter for OpenClaw."""
    tool_base = "openclaw.tools:Tool"
    skill_path = "~/.openclaw/skills"
    ...
```

未来支持 `--framework openclaw` 时用对应适配器。

## 五、当前 repo 与 plugin 的对应关系

| 当前路径 | Plugin 路径 | 提取方式 |
|---------|------------|---------|
| `nanobot/dnd/db/*` | `dnd_dm_plugin/db/*` | 移除 nanobot 框架依赖 |
| `nanobot/dnd/modules/*` | `dnd_dm_plugin/modules/*` | 直接复制 |
| `nanobot/dnd/rules/*` | `dnd_dm_plugin/rules/*` | 直接复制 |
| `nanobot/agent/tools/dnd_*.py` | `dnd_dm_plugin/tools/*` | 改为相对导入 |
| `nanobot/skills/dnd-*/` | `skills/*` | 直接复制 |
| `nanobot/templates/SOUL.md` 等 | `templates/*` | 直接复制 |
| `nanobot/skills/dnd-dm/dnd-engine/` | `dnd_dm_plugin/engine/` | 仅取 active 5 文件 |
| `nanobot/skills/dnd-dm/srd/` | `data/srd/` | 直接复制 |
| `references/DND.SRD.zh-CN/` | `data/srd-zh/` | 子模块 |
| `nanobot/channels/napcat.py` | `channels/napcat.py` | 直接复制 |

## 六、不纳入 Plugin 的部分

| 组件 | 原因 |
|------|------|
| `nanobot/agent/loop.py` 等框架代码 | 属于 NanoBot 本体 |
| `nanobot/providers/` | 同上 |
| `nanobot/session/` | 同上 |
| `nanobot/config/` | 同上 |
| `nanobot/channels/` (除 napcat) | 同上 |
| `scripts/start-*.bat/ps1` | 环境相关启动脚本 |
| `tools/NapCat.Shell/` | QQ 运行时，单独安装 |
| `localqq/` | 已废弃 |
| Engine 死代码 (6 个 DEPRECATED) | 不再维护 |
| `references/nanobotREADME.md` | 上游文档 |
| `docs/` (24 个上游文档) | 上游文档 |
| `webui/` | 上游 WebUI |
| `Dockerfile`, `docker-compose.yml` | 部署相关，可选 |

## 七、分阶段实施

### Phase 1: 提取核心层
- 创建 `dnd_dm_plugin` 包结构
- 提取 `db/`, `modules/`, `rules/` 并移除 nanobot 框架依赖
- 提取 `engine/`（仅 5 个 active 文件）

### Phase 2: 包装工具
- 移植 `dnd_*.py` 工具，改为相对导入
- 实现 `install` 命令（复制到目标 NanoBot 实例）

### Phase 3: 打包资源
- 复制 `skills/`, `templates/`, `data/`
- 实现 `ingest-srd` 和 `status` 命令

### Phase 4: 发布
- pip 包配置 (`pyproject.toml`)
- 安装文档
- 版本管理策略
