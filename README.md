# dnd-dm-agent

基于 [NanoBot](https://github.com/HKUDS/nanobot) 的 D&D 5e 地下城主 AI Agent。
默认集成 [dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill) 的规则引擎、
DM 人格。

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

## 快速开始

```powershell
# 1. 安装依赖
pip install -e .

# 2. 初始化工作区
nanobot onboard --wizard

# 3. 导入 D&D 规则库
python -m nanobot.dnd.db.cli rules ingest-srd

# 4. 启动
.\scripts\start-all.ps1
```

## 频道接入

### WebUI

`http://127.0.0.1:18765`

### Telegram

配置 `~/.nanobot/config.json`：
```json
{
  "channels": {
    "telegram": { "enabled": true, "token": "<bot-token>" }
  }
}
```

### QQ (NapCat)

QQ 通过 NapCat (OneBot v11 Forward WebSocket) 接入。

**首次安装：**

```powershell
.\scripts\setup-napcat.ps1
```

自动下载 NapCat + 便携 QQ，配置 WebSocket 服务。

**启动：**

```powershell
# 完整启动（NapCat QQ + gateway）
.\scripts\start-all.ps1

# 免扫码快速启动（已登录过）
.\scripts\start-all.ps1 -Quick

# 仅 gateway，不动 QQ
.\scripts\start-all.ps1 -NoQQ

# CPU 模式
.\scripts\start-all.ps1 -CpuOnly
```

或手动分步：
```powershell
localqq\start.ps1                              # 启动 NapCat + QQ
localqq\start-quick.ps1                        # 免扫码快速版
$env:DND_EMBEDDING_DEVICE="cuda"; nanobot gateway     # 启动 gateway
```

**nanobot 配置：**

```json
{
  "channels": {
    "napcat": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "allowFrom": ["<你的QQ号>"],
      "groupPolicy": "mention",
      "groupPolicyOverrides": {"<群号>": "mention"},
      "welcomeNewMembers": true
    }
  }
}
```

- `allowFrom` 仅控制私聊白名单
- 群聊权限由 `groupPolicy` 独立控制（`mention` / `open`），不受 `allowFrom` 限制

**权限修复：** 上游 NanoBot 对私聊群聊统一校验 `allowFrom`，群内非白名单用户 @机器人 会被静默丢弃。`nanobot/channels/napcat.py` 已覆写 `_handle_message`：私聊走白名单，群聊交 `groupPolicy` 裁决。

## 频道格式策略

各频道的回复格式由系统提示词按运行时分发，核心原则：

| 频道 | 策略 |
|------|------|
| NapCat (QQ) |纯文本 + emoji + `【】` 强调，**禁用** markdown bold/italic|
| Telegram | 短段落，`**bold**` 少量使用 |
| WebUI | Markdown 完整支持 |
| CLI | 纯文本 |

QQ 频道模板位于 `nanobot/templates/agent/identity.md`。

## 模组导入

支持 Markdown (.md)、PDF、DOCX、PPTX、XLSX 等格式。非 Markdown 文件通过 `markitdown`
转 Markdown 后入库；PDF 不使用 markitdown，而使用专有的结构解析器。

### PDF 解析

`nanobot/dnd/modules/pdf_parser.py` 提供布局感知的 PDF→Markdown 转换：

- **分页与书签感知**：提取 PDF 书签作为章节/附录锚点，每段文本保留源页码
- **CJK 重排**：检测并合并因排版断行产生的碎片行
- **去噪**：自动去除重复出现的页眉/页脚/页码线
- **目录过滤**：识别并与正文章节匹配的书签被优先保留，纯目录项不进入检索

```powershell
# 导入 PDF 模组
python -m nanobot.dnd.db.cli module import --campaign <id> --source "<path>/*.pdf" --activate
```

导入时自动拆分章节（支持中文数字章节、`ch.1`、`附录A` 等格式），Front Matter / 附录
标记为 `reference`，首个非前置章节标记为 `current`，其余为 `locked`。

### 分块策略

`nanobot/dnd/modules/chunking.py` 按以下规则切块：

| 参数 | 值 |
|------|-----|
| 块大小上限 | 1200 字 |
| 重叠量 | ≈100 字 |
| 边界约束 | 不允许跨标题边界切块 |
| 特殊类型 | `room`（房间标题）、`statblock`（属性块）、`list`（列表/表格） |
| 页码范围 | 每个 chunk 保留 `page_start`/`page_end`，支持溯源 |

目录项（`toc`）不进入 Dense 向量检索。

## 规则检索

2700+ 规则块，`BAAI/bge-m3` (1024 维) 语义索引，混合 FTS + 精确名称 + Dense Vector 检索。

```powershell
# 索引状态
python -m nanobot.dnd.db.cli rules status

# 搜索
python -m nanobot.dnd.db.cli rules search --campaign <id> --query "grapple escape" --top-k 5
```

运行时通过常驻 `dnd_rules` 工具调用，GPU 加速（`DND_EMBEDDING_DEVICE=cuda`）。

### 场景索引导出

从已入库的模组内容导出场景索引 JSON，格式兼容
`references/dnd-dm-skill/srd/scenes_index.json`：

```powershell
# 导出到 stdout
python -m nanobot.dnd.db.cli module export-scenes --campaign <id>

# 导出到文件
python -m nanobot.dnd.db.cli module export-scenes --campaign <id> --output scenes.json
```

场景检测自动适配标题层级（H2/H3/H4），包含 `type: "room"` 子节标注和
中英文混合标签（intro/exploration/dungeon/combat/social/transition）。

## 战役管理

战役状态以数据库为唯一权威源，支持完整 Snapshot 存档与恢复。

```powershell
# 创建战役
python -m nanobot.dnd.db.cli campaign create --name "博德之门" --module "BGDIA"

# 创建初始存档（必须）
python -m nanobot.dnd.db.cli save create --campaign <id> --label "初始状态" --workspace "<workspace>"

# 列出存档
python -m nanobot.dnd.db.cli save list --campaign <id>

# 恢复
python -m nanobot.dnd.db.cli save load --campaign <id> --slot <n> --workspace "<workspace>"
```

DM Agent 会通过 `dnd-dm` + `dnd-campaign-manager` Skill 自动执行这些流程。

## 上下文管理

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `maxMessages` | 40 | 回放历史上限 |
| `consolidationRatio` | 0.3 | 达 30% 窗口触发压缩 |
| `contextWindowTokens` | 65536 | 模型上下文窗口 |
| `idleCompactAfterMinutes` | 5 | 空闲压缩等待 |
| Dream | 每 2h | Long-term memory 总结 |

三层架构：Session JSONL（实时对话）→ Auto-Compact（token 预算压缩）→ Dream（定时总结到 MEMORY.md）。

## 项目结构

```
DM_agent/
├── nanobot/                  # Agent 运行时
│   ├── agent/                # Agent Loop · Context · Memory · Runner
│   ├── channels/             # napcat · telegram · websocket · ...
│   ├── dnd/                  # D&D 适配层 (rules · db · engine · modules)
│   ├── skills/               # dnd-dm · dnd-campaign-manager · napcat-qq
│   └── templates/            # 系统提示模板 (identity · SOUL · platform_policy)
├── localqq/                  # NapCat + 便携 QQ 运行时 (.gitignore)
├── scripts/                  # 安装与启动脚本
│   ├── setup-napcat.ps1      # 一键安装 NapCat
│   └── start-all.ps1         # 一键启动
└── tests/                    # 测试
```

## License

MIT
