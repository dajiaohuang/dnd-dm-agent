# D&D DM Agent — 多平台 Plugin 打包计划

将 dnd-dm-agent 的所有自定义内容打包为 OpenClaw、NanoBot、Hermes 三平台的
原生 plugin，遵循各平台 plugin 范式。不使用 MCP。

---

## 一、平台 Plugin 范式对比

| | OpenClaw | NanoBot | Hermes |
|---|----------|---------|--------|
| **Plugin 格式** | `openclaw.plugin.json` + `index.ts` (JS/TS 原生插件) | `skills/<name>/SKILL.md` + `agent/tools/*.py` 自动发现 | `plugin.yaml` + `__init__.py` → `ctx.register_tool()` |
| **Skill 定义** | `SKILL.md`（YAML frontmatter） | `SKILL.md`（YAML frontmatter + `always: true/false`） | `SKILL.md`（YAML frontmatter + `version`） |
| **工具注册** | `api.registerTool()` in plugin entry | Python Tool 类，package scan 自动注册 | `ctx.register_tool()` |
| **安装方式** | `openclaw plugins install <path>` | 复制目录到 `~/.nanobot/` | pip install 或手动复制到 `~/.hermes/plugins/` |
| **命名空间** | 无（全局 tool name） | 无（全局 tool name） | `plugin:skill` 前缀 |

---

## 二、公共资源层（三平台共享）

```
dnd-dm-resources/
├── skills/                      # 4 个 SKILL.md
│   ├── dnd-dm/
│   │   ├── SKILL.md            # DM 人格（always: true / 核心 Skill）
│   │   └── references/
│   │       ├── DM_RULES.md
│   │       ├── DM_TEMPLATES.md
│   │       ├── CHAR_CREATION.md
│   │       ├── MODULE_INDEX.md
│   │       └── MODULE_ARC.md
│   ├── dnd-campaign-manager/
│   │   └── SKILL.md
│   ├── dnd-module-gen/
│   │   └── SKILL.md
│
├── templates/                   # SOUL 模板
│   ├── SOUL.md                  # 明萨拉·班瑞 DM 人格
│   ├── IDENTITY.md
│   ├── AGENTS.md
│   ├── agent/
│   │   └── identity.md          # 运行时注入
│   └── memory/
│       └── MEMORY.md
│
├── domain/                      # D&D 业务逻辑（三平台共享 Python 代码）
│   ├── db/                      # Database + 8 services + 18 models + 6 migrations
│   ├── modules/                 # chunking + pdf_parser + scene_utils + search
│   ├── rules/                   # embedding + parser + ingest + search
│   └── engine/                  # dice + checks + resolve + xp + templates
│
├── data/                        # 规则数据
│   ├── srd/                     # SRD 5.2.1 英文 (20 文件)
│   └── srd-zh/                  # SRD 中文 (子模块)
│
└── cli/
    └── cli.py                   # JSON CLI（维护用，非 agent 调用）
```

---

## 三、NanoBot Plugin（基准实现）

NanoBot 就是当前代码库的形态，是最完整的实现。直接作为三平台的基准。

### 目录结构

```
dnd-dm-nanobot/
├── skills/           → 复制自 dnd-dm-resources/skills/
├── templates/        → 复制自 dnd-dm-resources/templates/
├── domain/           → 复制自 dnd-dm-resources/domain/  → ~/.nanobot/dnd/
├── tools/            → 4 个 Tool 类 → ~/.nanobot/agent/tools/
│   ├── dnd_campaign.py
│   ├── dnd_module.py
│   ├── dnd_rules.py
│   └── dnd_save.py
├── data/             → SRD 数据
├── cli/              → JSON CLI
│
├── install.sh        # 一键部署脚本
└── README.md
```

### `install.sh`

```bash
#!/bin/bash
NANOBOT="$HOME/.nanobot"
cp -r skills/*     "$NANOBOT/skills/"
cp -r tools/*.py   "$NANOBOT/agent/tools/"
cp -r templates/*  "$NANOBOT/templates/"
cp -r domain/*     "$NANOBOT/dnd/"
cp -r data/srd     "$NANOBOT/dnd/data/srd/"
echo "D&D DM Agent installed. Restart gateway."
```

### Skill 设置

```yaml
# dnd-dm: always: true → 每轮对话自动注入
# dnd-campaign-manager: always: false → agent 按需 read_file
# dnd-module-gen: always: false
```

---

## 四、OpenClaw Plugin

OpenClaw 原生插件使用 `openclaw.plugin.json` + TypeScript 入口。Python 业务逻辑通过 child process 调用。

### 目录结构

```
dnd-dm-openclaw/
├── openclaw.plugin.json       # 插件声明
├── package.json
├── index.ts                   # 入口：注册 tools + skills
├── skills/                    # → dnd-dm-resources/skills/
├── templates/                 # → dnd-dm-resources/templates/
├── python/                    # Python 业务层（复制 domain/）
│   ├── db/
│   ├── modules/
│   ├── rules/
│   └── engine/
├── cli_driver.py              # Python CLI 桥接（TypeScript → Python subprocess）
└── data/
    └── srd/
```

### `openclaw.plugin.json`

```json
{
  "schema": "1.0",
  "name": "dnd-dm",
  "displayName": "D&D Dungeon Master",
  "description": "D&D 5e campaign system — database, modules, dice, rules",
  "version": "1.0.0",
  "entry": "./index.ts",
  "capabilities": {
    "tools": true,
    "skills": true
  }
}
```

### `index.ts`（核心）

```typescript
import { PluginApi } from "@openclaw/plugin-sdk";
import { execSync } from "child_process";

function dndCli(action: string, args: Record<string, any>): any {
  const json = JSON.stringify({ action, ...args });
  const result = execSync(`python cli_driver.py '${json}'`, {
    cwd: __dirname,
    encoding: "utf-8",
  });
  return JSON.parse(result);
}

export default function (api: PluginApi) {
  // ---- register 4 native tools ----
  api.registerTool({
    name: "dnd_campaign",
    description: "Create and manage D&D campaigns — start, list, show, delete, set_status",
    parameters: { type: "object", properties: { action: { type: "string", enum: ["start","create","list","show","set_status","delete"] }, name: { type: "string" }, campaign_id: { type: "string" }, module_name: { type: "string" }, source_path: { type: "string" } }, required: ["action"] },
    handler: async (args) => dndCli("campaign", args),
  });

  api.registerTool({
    name: "dnd_save",
    description: "Save and restore campaign snapshots",
    parameters: { type: "object", properties: { action: { type: "string", enum: ["create","list","verify","restore","delete","export"] }, campaign_id: { type: "string" }, slot: { type: "integer" }, label: { type: "string" } }, required: ["action","campaign_id"] },
    handler: async (args) => dndCli("save", args),
  });

  api.registerTool({
    name: "dnd_module",
    description: "Import, search, and navigate module content",
    parameters: { type: "object", properties: { action: { type: "string", enum: ["import","index","search","expand","set_scene","current","status"] }, campaign_id: { type: "string" }, query: { type: "string" }, source_path: { type: "string" }, module_name: { type: "string" } }, required: ["action"] },
    handler: async (args) => dndCli("module", args),
  });

  api.registerTool({
    name: "dnd_rules",
    description: "Search the indexed D&D 5e SRD rule corpus",
    parameters: { type: "object", properties: { action: { type: "string", enum: ["search","expand","status"] }, query: { type: "string" }, campaign_id: { type: "string" }, top_k: { type: "integer" } }, required: ["action"] },
    handler: async (args) => dndCli("rules", args),
  });

  // ---- register skills ----
  api.registerBundleSkill("dnd-dm", "./skills/dnd-dm");
  api.registerBundleSkill("dnd-campaign-manager", "./skills/dnd-campaign-manager");
  api.registerBundleSkill("dnd-module-gen", "./skills/dnd-module-gen");

  // ---- register SOUL ----
  api.registerSoul("minthara-dm", "./templates/SOUL.md");
}
```

### `cli_driver.py`（TypeScript ↔ Python 桥）

```python
"""Single-entry JSON CLI bridge for OpenClaw plugin."""
import sys, json
from dnd_dm_openclaw.db.database import Database
from dnd_dm_openclaw.db.campaigns import CampaignService
# ... import all services ...

db = Database()
campaigns = CampaignService(db)
# ... instantiate all services ...

HANDLERS = {
    "campaign": { ... },
    "save": { ... },
    "module": { ... },
    "rules": { ... },
}

if __name__ == "__main__":
    args = json.loads(sys.argv[1])
    area = args.pop("area")
    result = HANDLERS[area][args["action"]](args)
    print(json.dumps(result, ensure_ascii=False, default=str))
```

---

## 五、Hermes Plugin

### 目录结构

```
dnd-dm-hermes/
├── __init__.py              # register(ctx)
├── plugin.yaml
├── skills/                  # → dnd-dm-resources/skills/
├── templates/               # → dnd-dm-resources/templates/
├── python/                  # → dnd-dm-resources/domain/
├── tools.py                 # 工具注册适配
├── data/
│   └── srd/
└── db_schema/
    └── migrations/
```

### `plugin.yaml`

```yaml
name: dnd-dm
version: 1.0.0
description: D&D 5e Dungeon Master agent system
capabilities:
  tools: true
  skills: true
platforms: [macos, linux, windows]
```

### `__init__.py`

```python
from pathlib import Path

def register(ctx):
    # ---- register 4 tools ----
    from .tools import (
        dnd_campaign_schema, dnd_campaign_handler,
        dnd_save_schema, dnd_save_handler,
        dnd_module_schema, dnd_module_handler,
        dnd_rules_schema, dnd_rules_handler,
    )
    ctx.register_tool("dnd_campaign", dnd_campaign_schema, dnd_campaign_handler)
    ctx.register_tool("dnd_save", dnd_save_schema, dnd_save_handler)
    ctx.register_tool("dnd_module", dnd_module_schema, dnd_module_handler)
    ctx.register_tool("dnd_rules", dnd_rules_schema, dnd_rules_handler)

    # ---- register bundled skills ----
    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(f"dnd-{child.name}", skill_md)

    # ---- inject SOUL into agent identity ----
    soul = (Path(__file__).parent / "templates" / "SOUL.md").read_text()
    ctx.inject_message(soul, role="system")
```

### `tools.py`（适配 Hermes tool schema）

```python
from .python.db.campaigns import CampaignService
from .python.db.database import Database

db = Database()

DND_CAMPAIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["start", "create", "list", "show", "set_status", "delete"]},
        "name": {"type": "string"},
        "campaign_id": {"type": "string"},
        "module_name": {"type": "string"},
        "source_path": {"type": "string"},
        "status": {"type": "string", "enum": ["active", "archived"]},
    },
    "required": ["action"],
}

def dnd_campaign_handler(args):
    svc = CampaignService(db)
    action = args["action"]
    if action == "start":
        return asdict(svc.start(args["name"], campaign_id=args.get("campaign_id"), ...))
    # ...
```

---

## 六、三平台对比总结

| | NanoBot | OpenClaw | Hermes |
|---|---------|----------|--------|
| **工具实现** | Python Tool 类，框架自动发现 | `api.registerTool()` in TS | `ctx.register_tool()` in Python |
| **Skill 加载** | `always: true` → 自动注入 | bundle skill → 按需加载 | `ctx.register_skill()` → `skill_view()` |
| **管道** | Python → Python（同进程） | TS → Python subprocess（JSON 桥） | Python → Python（同进程） |
| **安装** | `install.sh` 复制 | `openclaw plugins install` | pip install |
| **复杂度** | 最低（已是当前形态） | 中（需写 TS 入口 + Python 桥） | 低（与 NanoBot 类似） |

---

## 七、分阶段实施

### Phase 1: 公共资源层提取
- 创建 `dnd-dm-resources/`
- 从当前代码库提取 `skills/`, `templates/`, `domain/`, `data/`

### Phase 2: NanoBot Plugin（基准）
- 就是当前代码库。补齐 `install.sh` 和缺失的 skill frontmatter。

### Phase 3: Hermes Plugin
- 创建 `plugin.yaml` + `__init__.py`
- `tools.py` 包装 domain 层 → `ctx.register_tool()`
- 测试 `pip install`

### Phase 4: OpenClaw Plugin
- 创建 `openclaw.plugin.json` + `index.ts`
- `cli_driver.py` JSON 桥
- 4 个 `api.registerTool()` + skills + SOUL

### Phase 5: 文档
- 各平台 README / 安装指南
- 首次运行脚本（SRD ingest、DB init）
