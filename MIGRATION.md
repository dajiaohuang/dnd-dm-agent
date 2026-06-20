# D&D DM Agent — 迁移计划

将 dnd-dm-agent 拆分为可移植组件，使其能安装到 OpenClaw、NanoBot、Hermes
或任何支持 MCP + Skill 的 agent 框架中。

## 架构总览

```
┌─────────────────────────────────────────────────┐
│  Agent 框架 (NanoBot / OpenClaw / Hermes / ...) │
├─────────────────────────────────────────────────┤
│  MCP Servers                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ dnd-db   │ │ dnd-dice │ │ dnd-search       │ │
│  │ (战役DB)  │ │ (骰子)    │ │ (规则/模组检索)   │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
├─────────────────────────────────────────────────┤
│  Skills (SKILL.md)                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ dnd-dm   │ │ camp-mgr │ │ napcat-qq        │ │
│  │ (DM人格)  │ │ (战役管理) │ │ (QQ频道格式)      │ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
├─────────────────────────────────────────────────┤
│  SOUL Templates                                  │
│  ┌────────────────────────────────────────────┐  │
│  │ SOUL.md + IDENTITY.md + AGENTS.md          │  │
│  │ (明萨拉·班瑞 DM 人格)                        │  │
│  └────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────┤
│  Data Bundles                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
│  │ SRD 5.2.1│ │ SRD zh-CN│ │ scenes_index.json│ │
│  └──────────┘ └──────────┘ └──────────────────┘ │
└─────────────────────────────────────────────────┘
```

---

## 一、MCP Servers（3 个）

### 1.1 `dnd-db` — 战役数据库 MCP Server

**最大、最核心的组件。** 封装所有数据库操作。

**文件来源：**

| 源文件 | 说明 |
|--------|------|
| `nanobot/dnd/db/database.py` | SQLAlchemy Database 类 |
| `nanobot/dnd/db/campaigns.py` | CampaignService |
| `nanobot/dnd/db/characters.py` | CharacterService |
| `nanobot/dnd/db/events.py` | CampaignEventService |
| `nanobot/dnd/db/snapshots.py` | CampaignSnapshotService |
| `nanobot/dnd/db/module_content.py` | ModuleImportService |
| `nanobot/dnd/db/module_progress.py` | ModuleProgressService |
| `nanobot/dnd/db/undo.py` | UndoManager |
| `nanobot/dnd/db/models/*.py` | 全部 7 个模型文件 |
| `nanobot/dnd/db/migrations/**/*.py` | 全部 6 个迁移 |
| `nanobot/dnd/modules/scene_utils.py` | 场景解析工具 |
| `nanobot/dnd/modules/chunking.py` | 分块逻辑 |
| `nanobot/dnd/modules/pdf_parser.py` | PDF 解析器 |
| `nanobot/dnd/modules/search.py` | ModuleSearchService |
| `nanobot/dnd/rules/embedding.py` | BgeM3Embedder |
| `nanobot/dnd/rules/parser.py` | Markdown 解析器 |
| `nanobot/dnd/rules/ingest.py` | RuleIngestService |
| `nanobot/dnd/rules/search.py` | RuleSearchService |

**依赖：** `sqlalchemy`, `alembic`, `pypdf`, `sentence-transformers`, `numpy`, `markitdown`

**MCP Tools：**

| Tool | 对应方法 | 说明 |
|------|---------|------|
| `campaign_start` | CampaignService.start() | 一键开团 |
| `campaign_create` | CampaignService.create() | 创建战役 |
| `campaign_list` | CampaignService.list() | 列出战役 |
| `campaign_get` | CampaignService.get() | 查看战役 |
| `campaign_set_status` | CampaignService.set_status() | active/archived |
| `campaign_delete` | CampaignService.delete() | 删除战役 |
| `character_create` | CharacterService.create() | 创建角色 |
| `character_list` | CharacterService.list() | 列出角色 |
| `event_create` | CampaignEventService.create() | 记录事件 |
| `event_list` | CampaignEventService.list() | 列出事件 |
| `snapshot_create` | CampaignSnapshotService.create() | 创建存档 |
| `snapshot_list` | CampaignSnapshotService.list() | 列出存档 |
| `snapshot_get` | CampaignSnapshotService.get() | 校验存档 |
| `snapshot_restore` | CampaignSnapshotService.restore() | 加载存档 |
| `snapshot_delete` | CampaignSnapshotService.delete() | 删除存档 |
| `snapshot_export` | CampaignSnapshotService.export() | 导出 JSON |
| `module_import` | ModuleImportService.import_path() | 导入模组 |
| `module_list` | ModuleImportService.list() | 列出模组 |
| `module_index` | ModuleImportService.index() | 场景索引 |
| `module_scene` | ModuleImportService.read_scene() | 读取场景 |
| `module_export_index` | ModuleImportService.export_scene_index() | 导出 scene JSON |
| `module_delete` | ModuleImportService.delete() | 删除模组 |
| `module_search` | ModuleSearchService.search() | 搜索模组 |
| `module_set_scene` | ModuleProgressService.set_scene() | 设置当前场景 |
| `module_current` | ModuleProgressService.current() | 当前场景 |
| `rules_ingest` | RuleIngestService.ingest_srd() | 导入规则 |
| `rules_search` | RuleSearchService.search() | 搜索规则 |
| `rules_expand` | RuleSearchService.expand() | 展开规则 |
| `rules_status` | RuleSearchService.status() | 规则状态 |
| `undo` | UndoManager.undo() | 撤销操作 |

---

### 1.2 `dnd-dice` — 骰子与战斗计算 MCP Server

**无状态的纯计算工具。** 不依赖数据库。

**文件来源：**

| 源文件 | 说明 |
|--------|------|
| `nanobot/skills/dnd-dm/dnd-engine/src/dnd_engine/dice/rolls.py` | d20、任意骰、表达式 |
| `nanobot/skills/dnd-dm/dnd-engine/src/dnd_engine/combat/checks.py` | 技能/豁免/攻击检定 |
| `nanobot/skills/dnd-dm/dnd-engine/src/dnd_engine/combat/resolve.py` | 命中/伤害/DC 计算 |
| `nanobot/skills/dnd-dm/dnd-engine/src/dnd_engine/party/xp.py` | XP 计算 |
| `nanobot/skills/dnd-dm/dnd-engine/src/dnd_engine/save/templates.py` | 角色/怪物模板 |

**依赖：** 仅标准库 `random`

**MCP Tools：**

| Tool | 说明 |
|------|------|
| `roll_d20` | d20 检定（优势/劣势） |
| `roll_dice` | 任意骰（3d6+2 等） |
| `skill_check` | 技能检定 |
| `save_check` | 豁免检定 |
| `attack_check` | 攻击命中检定 |
| `calc_damage` | 伤害计算 |
| `calc_save_dc` | 豁免 DC 计算 |
| `calc_xp` | 战斗/非战斗 XP |
| `level_up_xp` | 升级所需 XP |

---

### 1.3 `dnd-search` — 模组检索 MCP Server（可选独立）

**从 `dnd-db` 中拆出的只读检索层。** 如果不想装完整 DB，可以用这个轻量版。

**文件来源：** `nanobot/dnd/modules/search.py` + `nanobot/dnd/rules/search.py` + `nanobot/dnd/rules/embedding.py`

**MCP Tools：** `module_search`, `module_expand`, `rules_search`, `rules_expand`, `rules_status`

---

## 二、Skills（SKILL.md）

可以直接复制到目标框架的 skills 目录。

### 2.1 `dnd-dm` Skill

**`nanobot/skills/dnd-dm/SKILL.md`**

核心 DM 人格 Skill。定义：
- 7 步裁决循环
- Engine 函数 API 参考
- 权威数据源顺序
- Snapshot 工作流
- 上下文管理
- 禁止事项

**引用文件（skill 内部）：**
- `references/DM_RULES.md` — 详细裁判规则
- `references/DM_TEMPLATES.md` — 输出格式模板
- `references/CHAR_CREATION.md` — 角色创建
- `references/MODULE_INDEX.md` — 模组索引
- `references/MODULE_ARC.md` — 模组弧线

### 2.2 `dnd-campaign-manager` Skill

**`nanobot/skills/dnd-campaign-manager/SKILL.md`**

战役生命周期管理。定义开团流程、模组导入、存档操作。

### 2.3 `napcat-qq` Skill

**`nanobot/skills/napcat-qq/SKILL.md`**

QQ 频道格式策略。纯文本 + emoji + `【】` 强调。

---

## 三、SOUL / 人格模板

**5 个文件定义完整 DM 人格：**

| 文件 | 内容 |
|------|------|
| `nanobot/templates/SOUL.md` | 明萨拉·班瑞 DM 人格核心：守序邪恶、规则严苛、结论前置、冷幽默 |
| `nanobot/templates/IDENTITY.md` | 身份约束：2024 规则书、章节锁、里程碑等级 |
| `nanobot/templates/AGENTS.md` | 会话启动协议、记忆管理、心跳维护 |
| `nanobot/templates/agent/identity.md` | 运行注入的 identity 提示 |
| `nanobot/templates/memory/MEMORY.md` | 长期记忆模板（预置 D&D 分区） |

**移植方式：** 合并为一个 SOUL 文件，放入目标框架的 templates/souls 目录。

---

## 四、Data Bundles

### 4.1 规则数据

| 来源 | 内容 | 大小 |
|------|------|------|
| `nanobot/skills/dnd-dm/srd/references/DND5eSRD_*.md` | SRD 5.2.1 英文 (CC-BY-4.0) | ~1.5MB / 20 文件 |
| `references/DND.SRD.zh-CN/` | SRD 中文翻译 | ~2MB |

首次启动时通过 `rules_ingest` MCP tool 导入数据库。

### 4.2 模组数据

| 来源 | 内容 |
|------|------|
| `references/dnd-dm-skill/srd/scenes_index.json` | Baldur's Gate: Descent into Avernus 场景索引 |

导入 PDF 后自动生成。

---

## 五、不纳入迁移的部分

| 组件 | 原因 |
|------|------|
| `nanobot/channels/napcat.py` | 框架特定，每个 agent 框架有自己的 channel 实现 |
| `scripts/*.bat`, `scripts/*.ps1` | 启动脚本，环境相关 |
| `localqq/`, `tools/NapCat.Shell/` | QQ 运行时，需单独安装 |
| Engine 死代码（6 个 DEPRECATED 文件） | 已被 DB 取代 |
| `nanobot/agent/tools/dnd_*.py` | 当前 NanoBot Tool 实现，迁移后由 MCP Server 替代 |
| `nanobot/dnd/db/cli.py` | CLI，迁移后由 MCP Server 替代 |

---

## 六、安装步骤（以 OpenClaw 为例）

### Step 1: 安装 Python 包

```bash
pip install dnd-dm-mcp
```

包含 3 个 MCP Server + 全部依赖。

### Step 2: 配置 MCP Server

```yaml
# openclaw.yaml
mcp_servers:
  dnd-db:
    command: python -m dnd_dm_mcp.db
    env:
      DND_DATABASE_URL: sqlite+pysqlite:///./dnd_dm.db
  dnd-dice:
    command: python -m dnd_dm_mcp.dice
```

### Step 3: 安装 Skills

```bash
cp -r skills/dnd-dm ~/.openclaw/skills/
cp -r skills/dnd-campaign-manager ~/.openclaw/skills/
cp -r skills/napcat-qq ~/.openclaw/skills/
```

### Step 4: 安装 SOUL

```bash
cp souls/minthara-dm.md ~/.openclaw/souls/
```

### Step 5: 导入规则数据

```bash
python -m dnd_dm_mcp.cli rules ingest-srd --path ./data/srd/
```

### Step 6: 导入模组

```python
# 通过 MCP tool 调用
dnd-db.module_import(campaign_id="...", source_path="./BGDIA.pdf", module_name="BGDIA")
```

---

## 七、分阶段实施建议

### Phase 1: 抽取 MCP Server（核心）

1. 创建 `dnd_dm_mcp` Python 包
2. 将 `nanobot/dnd/db/` → `dnd_dm_mcp/db/`
3. 将 `nanobot/dnd/modules/` → `dnd_dm_mcp/modules/`
4. 将 `nanobot/dnd/rules/` → `dnd_dm_mcp/rules/`
5. 移除对 `nanobot` 框架的依赖（仅保留 `Database` 类）
6. 用 FastMCP 包装为 MCP Server
7. 提取 `dnd-dice` 作为独立 MCP Server

### Phase 2: 标准化 Skills

1. 将 `SKILL.md` 中的 `python -m nanobot.dnd.db.cli` 替换为 MCP tool 调用
2. 移除 NanoBot 特定的 `dnd_module`/`dnd_rules` tool 引用
3. 添加跨框架兼容性说明

### Phase 3: 打包 SOUL

1. 合并 5 个 SOUL 文件为单文件
2. 去除框架特定指令
3. 添加 SOUL 安装文档

### Phase 4: 发布

1. 发布 `dnd-dm-mcp` 到 PyPI
2. 提供 OpenClaw / NanoBot / Hermes 安装指南
3. 提供 Docker Compose 一键部署
