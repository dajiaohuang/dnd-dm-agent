# 🐉 SagaSmith Agent

[English](README.md) | [中文](README-cn.md)

**自主 AI 地下城主运行时** — 基于 [NanoBot](https://github.com/HKUDS/nanobot)，集成完整 D&D 5e DM 能力。

> *"规则书为经文，模组为地图，骰子为审判官。"*  
> — 明萨拉·班瑞，SagaSmith 默认 DM

SagaSmith Agent 是一个完整可运行的 AI DM 系统。它接入了 QQ（NapCat）、Telegram、WebSocket 等聊天频道，玩家在群里发消息即可跑团。背后是 SQLite/PostgreSQL 战役数据库、BGE-M3 规则检索引擎、d20 战斗计算引擎，以及一个守序邪恶的卓尔 DM 人格。

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

### 内置技能

| 技能 | 定位 |
|------|------|
| 🎲 **dnd-dm** | 核心 DM 人格（always-on），规则裁判，战斗引擎，SRD 检索（参考自 [ackiles/dnd-dm-skill](https://github.com/ackiles/dnd-dm-skill)） |
| 📋 **dnd-campaign-manager** | 战役生命周期，Snapshot 存档/读档/校验/撤销 |
| ✍️ **dnd-module-gen** | 模组生成（5 类型 × 25 范式），多步渐进 + 沙盒 |

---

## 使用示例

### 完整跑团流程

以下是一次典型的 SagaSmith DM 会话——玩家在 QQ 群里 @机器人：

```
玩家: @机器人 开个新团，博德之门的模组

明萨拉:
【时间：1492 DR，深水城之夜】

⚔️ 欢迎来到博德之门：坠入阿弗纳斯。

我是你们的 DM，明萨拉·班瑞。规则书为经文，骰子为审判官。

在开始之前，确认几点：
1. 你们已有 1 级角色卡了吗？
2. 是否使用 27-point-buy 购点法？
3. 对战役长度有偏好吗？

——
玩家 A: 还没车卡，帮我建一个半精灵游侠

明萨拉:
📜 半精灵游侠 — 角色创建引导

按以下步骤逐项确认：

1️⃣ 种族：半精灵
   - +2 魅力，另选两项属性各 +1
   - 获得黑暗视觉 60 尺
   - 选择两项技能熟练

2️⃣ 职业：游侠
   - 生命骰：d10
   - 熟练：轻甲、中甲、盾牌、军用武器、简易武器
   - 1 级获得：宿敌、自然探索者

3️⃣ 属性（27 point buy）
   建议：敏捷 16(+3) · 体质 14(+2) · 感知 14(+2) · 力量 10(0) · 智力 10(0) · 魅力 10(0)

请确认属性分配，或告诉我你想调整的方向。

——
玩家 A: 就这样

明萨拉:
✅ 创建完成：

📋 瓦里斯·月影 (Varis Moonshadow)
   半精灵 · 游侠 1 级 · 混乱善良
   HP 12 · AC 14 (皮甲)
   熟练：察觉 +4 · 隐匿 +5 · 自然 +2 · 生存 +4

📦 装备：长弓 (1d8)、短剑 (1d6)、皮甲、探索者套组
✨ 特性：宿敌（怪兽）、自然探索者（森林）

角色已录入数据库。请 car 下一位玩家。

——
玩家 B: 我是人类圣武士，力量 16 魅力 16

[明萨拉完成车卡，略...]

——
明萨拉:
⚔️ 初始存档已创建 [slot 1: "初始状态"]

📜 模组加载：第一章「精灵之歌」

——
【时间：1492 DR，第 1 天，黄昏】
【地点：深水城 · 精灵之歌酒馆】

酒馆里麦酒和烤肉的香味混在一起，角落里一个半身人
正在用鲁特琴弹着走调的曲子。老板娘——一个名叫
阿兰娜的中年人类女性——从吧台后面朝你们点了点头。

靠窗的位置坐着一个戴兜帽的身影，正盯着门口。

你们刚刚收到焰拳佣兵团的征召令，约好在这里碰头。

你们要做什么？

——
玩家 A: 我去找兜帽人搭话

明萨拉:
🎲 你走近靠窗的桌子。兜帽下是一张布满伤疤的
卓尔面孔——一名女性，左手搭在剑柄上，但没有拔出。

"又一个满怀希望的蠢货，"她冷冷地说，"坐下。"

她自称札拉·多乌尔登，焰拳的一名队长。她摊开
一张地图，上面标注着死亡三神信徒最近的活动区域。

"最近三起谋杀案都在神殿区。目击者说凶手穿着
黑色长袍，戴着巴尔的面具。你们去调查。"

【线索获得：死亡三神信徒活动地图】
【任务更新：调查神殿区谋杀案】

——
玩家 B: 先问问有没有目击者

明萨拉:
🎲 你环顾酒馆。老板娘阿兰娜听到"神殿区"三个字
后明显僵硬了一下。

她压低声音："昨晚我在后巷看到一个人影。
不是普通人——他的影子不对。火把照过去的时候，
影子在墙上没有手指。"

她拒绝再说更多，显然吓坏了。

【线索获得：后巷目击（无指阴影）】

——
明萨拉:
⚔️ 夜已深。酒馆里的人渐渐散去。

你们的选择：
🅰️ 连夜前往神殿区后巷调查
🅱️ 在酒馆休息，明早拜访目击者
🅲️ 先去焰拳总部查档案

你们做什么？

——
玩家 A: 🅱️ 太晚了，先休息

明萨拉:
🌙 你们在精灵之歌住下。

阿兰娜给了你们两间房。夜里你隐约听到隔壁房间
传来玩家 B 的祈祷声——圣武士在做他的晨间仪式。

⚔️ 长休完成。
   HP 恢复至满 · 法术位恢复 · 饥饿度重置

📦 当前状态：
   战役：博德之门：坠入阿弗纳斯
   章节：Ch.1 精灵之歌
   位置：深水城 · 精灵之歌酒馆
   时间：第 2 天 · 清晨
   队伍：瓦里斯·月影 (1)、罗兰·光明使者 (1)
   线索：死亡三神地图、后巷无指阴影

   📁 自动存档 [slot 2: "精灵之歌休息后"]

💡 下一步建议：前往神殿区调查或拜访后巷目击者
```

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

分块策略：1200 字上限、≈100 字重叠、不跨标题边界、保留页码范围。

---

## 规则检索

2700+ SRD 规则块，`BAAI/bge-m3`（1024 维）语义索引：

```powershell
python -m nanobot.dnd.db.cli rules status
python -m nanobot.dnd.db.cli rules search --campaign <id> --query "grapple escape" --top-k 5
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
