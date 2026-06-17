# DND DM Agent

一个面向长期 D&D 5E 跑团的本地优先 LLM Agent 系统。
通过 **OpenAI Function-Calling 工具架构**，LLM 理解自然语言意图后调用 Python 工具进行
确定性投骰、状态计算和结构化数据写入。角色卡、NPC、物品、效果、战役设定和战斗状态
全部保存在 SQLite/PostgreSQL 中，可查询、可审计、可回滚。

## 三种模式

| 模式 | play_style | 说明 |
|------|-----------|------|
| **游戏外 (Lobby)** | `lobby` | 管理战役、车卡、编辑设定。可无当前战役 |
| **DM 模式** | `campaign` | AI 叙事 DM。描写环境、扮演 NPC、推进剧情 |
| **骰娘模式** | `dice_assistant` | 工具型骰娘。纯机械结算、检定、战斗主持 |

启动默认进入 lobby。发送 `/进入DM` 或 `/进入骰娘` 开始游戏，`/退出` 返回 lobby。
三种模式共享同一个 "当前战役"，切换战役用 `/切换战役 名称`。

## LLM Agent 工具架构

```
用户: "帮我创建卡利恩，3级人类法师，力量16敏捷14"
  → LLM 理解意图 → tool_call create_character_quick(name="卡利恩", ...)
  → characters 表写入 + 自动绑定 QQ

用户: "我用长剑攻击地精"
  → LLM → combat_attack(target="地精", weapon="长剑")
  → 读 HotSnapshot → checked_roll("1d20+7") → DiceAuditLog

用户: "2d6+3" (无 @)
  → 被动骰子监听 → @发送者 "🎲 2d6+3 = 11"
```

全部 53 个 LLM 工具覆盖：战役管理、角色卡、设定编辑、检定、战斗行动、撤销、绑定导出。

## 战斗系统

详细说明：[战斗系统](docs/COMBAT_SYSTEM-cn.md)

### 回合动作配额

回合内动作由系统追踪配额，不会自动推进回合:

```
轮到卡利恩 → 配额: 主动作1 附赠1 移动30
  attack → 主动作: 1→0 → "命中8点。剩余: 附赠1 移动30"
  动作如潮 → use_feature("action_surge") → extra_actions: 0→1
  attack → extra_actions: 1→0 → "命中12点。剩余: 附赠1 移动30"
  end_turn → advance_turn → "轮到地精"
```

| 战斗工具 | 消耗 | 说明 |
|---------|------|------|
| `combat_attack` | main/bonus/extra | 武器攻击，自动 d20+加值+伤害 |
| `combat_cast_spell` | main/bonus/extra | 施法，法术DC/攻击+豁免 |
| `combat_dash/disengage/dodge` | main | 疾走/撤退/闪避 |
| `combat_ability_check` | main | 推撞/擒抱等 |
| `use_feature` | 特性决定 | 动作如潮/回气/狂暴 |
| `end_turn` | — | 结束回合推进 |
| `turn_status` | — | 查询剩余配额 |
| `ask_clarification` | — | 追问缺信息(不消耗) |

### 热数据层

每次行动都从 `get_hot_character()` 读取实时机械快照——基础角色卡 + active_effects (buff/debuff/装备)。
所有投骰走 `checked_roll()`: `random.randint()` + `DiceAuditLog` 审计表。
HP 变更写入 `CharacterChange` 变更日志，支持 `undo_damage`/`undo_healing` 反推撤销。

### 骰娘自由战斗（非系统管理）

真人 DM 管回合，玩家自由 @bot 行动。骰娘从对话历史感知回合、提醒跳过、主动问要不要投先攻。
进入系统回合制需要 DM 明确确认。

## 快速开始

### 本地后端

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```powershell
Copy-Item .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY
cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8011
```

初始化规则库:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8011/ingest/rules
```

### Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

### 前端

```powershell
run_webui.bat     # http://127.0.0.1:3001
```

## QQ / NapCat

```text
login_napcat_dnd.bat        # 一键启动
```

NapCat OneBot HTTP Post URL: `http://127.0.0.1:8011/napcat/callback`

管理 QQ 绑定:

```powershell
manage_qq_bindings.bat bind 123456789 char_001 --name 玩家昵称
manage_qq_bindings.bat list
```

## 人物卡导入导出

- **导入**: QQ 发人物卡 xlsx → `parse_character_sheet_xlsx()` → 结构化 JSON → LLM 车卡
- **HTML/PDF/DOCX**: 上传附件 → `parse_files()` → 内容注入 LLM → 提取车卡
- **导出**: `/导出角色卡` → `export_character_sheet()` → 模板填数据 → xlsx 文件
- **附件持久化**: 最近 5 个附件存 `campaign.config.last_attachments`，`read_attachment` 工具跨消息引用

## 配置

```env
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

NAPCAT_BASE_URL=http://127.0.0.1:3000
NAPCAT_SELF_ID=1534055688
NAPCAT_DM_USER_IDS=2480933622
NAPCAT_REQUIRE_GROUP_AT=true
```

## 项目结构

```text
backend/app/
  main.py               FastAPI + NapCat callback
  services.py           统一 resolve_chat (lobby/DM/dice)
  message_router.py     消息分派
  dice_assistant.py     骰娘快速路径 + DM确认
  llm.py                DeepSeek API + tools
  llm_loop.py           LLM → tool_call → handler 执行循环
  campaign_turns.py     回合管理 + 动作配额
  campaign_control.py   战役控制命令
  character_build_flow.py 车卡工具函数
  qq_bindings.py        QQ绑定管理
  parsing/              多文件解析 (PDF/DOCX/HTML/XLSX/图片/音频)
  tools/
    hot_character.py    HotSnapshot 热数据 + checked_roll
    command_tools.py    53个工具 schema + handler 注册
    combat_tools.py     战斗行动 + use_feature/end_turn
    check_tools.py      检定/伤害/治疗/撤销/条件
    character_builder.py 人物卡构建 + 导入导出
    dice.py             roll_dice + checked_roll
  integrations/napcat.py NapCat HTTP + 文件上传
  db/models.py          数据模型 (含 DiceAuditLog, CharacterChange)
data/
  raw/                  人物卡模板 + 规则书
  generated/characters/ 导出的人物卡 xlsx
docs/
  COMBAT_SYSTEM-cn.md   战斗系统详细文档
frontend/               Next.js Web UI
```

## 测试

```powershell
cd backend
uv run pytest -q
```
