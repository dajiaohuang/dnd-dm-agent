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
| 📦 [SagaSmith-skills](https://github.com/dajiaohuang/SagaSmith-skills) | 跨平台 skill 插件包（NanoBot / OpenClaw / Hermes） |
| ✍️ [SagaSmith-module-gen-skill](https://github.com/dajiaohuang/SagaSmith-module-gen-skill) | 独立模组生成器（纯 Markdown skill） |

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

以下完整演示 SagaSmith 从零到跑团的全生命周期。每个阶段既是功能展示，也是 CLI / 对话两种交互方式的参考。

### 1. 规则书导入

```powershell
# CLI：首次安装后导入 SRD
python -m nanobot.dnd.db.cli rules ingest-srd
```

```
📚 SRD 5.2.1 导入完成
   规则块: 2,847 · 嵌入向量: 2,847 (BGE-M3, 1024 维)
   规则书: 玩家手册(2024) · 城主指南(2024) · 怪物图鉴(2025)

📊 索引状态: 就绪 ✅
```

### 2. 模组导入

支持 Markdown、PDF、DOCX、PPTX、XLSX。PDF 使用专有结构解析器（书签恢复 + CJK 重排）。

```
用户: 导入博德之门模组

Agent:
📄 导入中...
   ✅ 书签恢复: 42 个章节/附录锚点
   ✅ 目录过滤: 移除 8 个纯目录项
   ✅ 章节拆分: 5 章 · 62 场景 · 847 检索块

📦 完成：博德之门：坠入阿弗纳斯 (BGDIA)
   Ch.1 精灵之歌 · Ch.2 死亡三神地城
   Ch.3 范萨姆普尔别墅 · Ch.4 烛堡 · Ch.5 大教堂
   嵌入向量: 847 (BGE-M3) · 状态: 就绪
```

也可以通过 CLI：

```powershell
python -m nanobot.dnd.db.cli module import --campaign <id> --path "D:\modules\bgdia\" --name "BGDIA"
python -m nanobot.dnd.db.cli module index --campaign <id>
```

### 3. 开团流程

```
用户: 开个新团

Agent:
🎲 开团向导

1️⃣ 模组来源 — 数据库中已有：
   · BGDIA (5 章 / 62 场景)
   使用已有模组，还是导入新的？

2️⃣ 规则版本 — 已锁定 2024 核心规则 + SRD 5.2.1

3️⃣ 玩家与角色 — 请告诉我人数、偏好

用户: 就用 BGDIA，2 个玩家，我们自己车卡

Agent:
⚔️ 一键开团
   ✅ 战役创建: campaign_7a3f
   ✅ 规则绑定: 2024 Core
   ✅ 模组绑定: BGDIA · Ch.1 解锁
   ✅ 初始存档: slot 1 "初始状态"

   🎉 战役就绪！

   请创建角色——告诉我种族/职业/等级，
   或说"随机生成"我来 roll 属性。
```

### 4. 角色创建

```
用户: 帮我建一个半精灵游侠

Agent:
📜 角色创建 — 半精灵游侠

1️⃣ 种族：半精灵 (+2 CHA，另选两项 +1，黑暗视觉 60ft)
2️⃣ 职业：游侠 (d10，轻甲/中甲/盾牌熟练，宿敌、自然探索者)
3️⃣ 属性 (27 point-buy) 建议：DEX 16 · CON 14 · WIS 14

确认后继续...

用户: 就这样

Agent:
✅ 瓦里斯·月影 — 半精灵游侠 1 级 · 混乱善良
   HP 12 · AC 14 · 长弓 (1d8) · 短剑 (1d6)
   熟练：察觉 +4 · 隐匿 +5 · 自然 +2 · 生存 +4
   已录入数据库

📦 当前状态 [slot 1:]
   战役: BGDIA · Ch.1 · 深水城 · 队伍: 瓦里斯 (1)
```

### 5. 模组创作（在战役中生成子模组）

```
用户: 给我现在这个战役生成一个支线——下水道的鼠人巢穴

Agent:
✅ 生成支线模组：下水道的阴影╱The Sewer Shadows
   类型：One-shot (Five-Room Dungeon)
   CR：适合 2 级队伍

   结构预览:
   Scene 1 — 下水道入口（守卫: 鼠人哨兵 ×2, CR 1/2）
   Scene 2 — 黏滑隧道（技能挑战: DC 12 体操越过污水）
   Scene 3 — 被遗忘的神殿（反转: 鼠人非敌，是逃亡的奴隶）
   Scene 4 — 孵化室 Boss 战（鼠人王 CR 2 + 鼠群 ×3）
   Scene 5 — 获救学者（奖励: 下水道地图 + 100GP + 1 魔法物品）

   ✅ 已写入 modules/sewer_shadows.md
   ✅ 已导入战役: 5 场景 · 47 检索块

📜 支线可在当前章节中自然触发。
```

### 6. 带团过程

```
Agent:
【时间：1492 DR，黄昏】
【地点：深水城 · 精灵之歌酒馆】

酒馆里麦酒和烤肉的香味混在一起。老板娘阿兰娜
从吧台朝你们点了点头。靠窗坐着一个戴兜帽的身影，
正盯着门口。

你们做什么？

用户: 我去找兜帽人

Agent:
🎲 兜帽下的卓尔女性——札拉·多乌尔登，焰拳队长。

她摊开一张地图："三起谋杀案，都在神殿区。
凶手穿黑色长袍，戴巴尔面具。去查。"

【线索: 死亡三神活动地图】
【任务: 调查神殿区谋杀案】

用户: 先问问老板娘有没有目击

Agent:
🎲 阿兰娜压低声音："昨晚后巷一个人影...
火把照过去，影子在墙上没有手指。"

她吓坏了，拒绝多说。

【线索: 后巷目击（无指阴影）】

⚔️ 你们的选择:
🅰️ 连夜前往后巷调查  🅱️ 休息明早拜访目击者  🅲️ 去总部查档案

用户: 🅱️

Agent:
🌙 长休完成。HP/法术位恢复。

📦 战役状态 — Ch.1 · 深水城 · 第 2 天清晨
   瓦里斯·月影 (1) · 罗兰·光明使者 (1)
   线索: 地图、无指阴影
   📁 自动存档 [slot 2: "精灵之歌休息后"]
```

### 7. 规则提问

D&D 规则问题直接 @机器人查询，Agent 通过 BGE-M3 Dense Vector 检索 SRD：

```
用户: 冰巨人的 AC？特性？

Agent:
🔍 检索 SRD 5.2.1 怪物图鉴(2025)...
   Frost Giant · CR 8 · HP 138(12d12+60) · AC 15 (拼缀甲)
   特性: 寒冰抗性 · 多重攻击 (2×巨斧, +12, 3d12+6)
   来源: 怪物图鉴(2025) p.187-188


用户: 人类圣武士 5 级有几个法术位？

Agent:
🔍 检索玩家手册(2024)...
   📖 圣武士 5 级
   一环: 4 · 二环: 2
   已知法术: CHA mod + ½ 圣武士等级 (向下取整)
   来源: 玩家手册(2024) Ch.3


用户: 黑暗视觉战斗中有什么影响？

Agent:
🔍 检索...
   🌑 黑暗中感知检定劣势 · 不能辨色仅灰度
   攻击黑暗视觉内目标不受光照劣势
   来源: 玩家手册(2024) Ch.1 · 规则术语


用户: 火焰球的伤害和豁免？

Agent:
🔍 检索...
   🔥 Fireball · 三环塑能系
   射程 150ft · 20ft 半径 · 8d6 火焰伤害
   DEX 豁免 (DC = 施法者 DC) 成功减半
   来源: 玩家手册(2024) Ch.11
```

### 8. 战役管理

```
用户: 查看所有战役

Agent:
📋 活动战役
   campaign_7a3f  博德之门：坠入阿弗纳斯  Ch.1  2级  存档 2
   campaign_b21   冰风谷：冰霜少女        Ch.3  5级  存档 7

用户: 切换到冰风谷

Agent:
✅ 已切换 campaign_b21
   📍 Ch.3 Scene 4 · 布林山德 · 上次: 昨天

用户: 存个档

Agent:
✅ 存档完成
   📁 slot 8: "进入冰巨人之门前"
   📍 Ch.3 Scene 2 · 冰痕洞穴入口 · 🔑 SHA-256: a3f2c...

用户: 列表冰风谷存档

Agent:
📁 存档列表 (campaign_b21)
   slot 1 "初始状态"            创建时
   slot 2 "布林山德抵达"        Ch.1
   slot 3 "冬狼遭遇战后"        Ch.2
  ┊
   slot 8 "进入冰巨人之门前"    Ch.3 ← 当前

用户: 读档到 slot 5

Agent:
⚠️ 读档前自动保存当前 (slot 9)
⏪ 恢复 slot 5 "冰巨人之门开启前"
   ✅ 世界 · 队伍 · 战斗 · 剧情摘要 · 事件日志 全部恢复
   📍 Ch.3 Scene 2 · 冰巨人之门
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
