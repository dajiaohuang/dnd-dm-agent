# Session Handover — DND DM Agent v3.0

## 架构概览

```
QQ → NapCat → /napcat/callback (main.py)
  ├─ campaign=None → resolve_chat(mode="lobby")  # 游戏外大厅
  ├─ play_style="campaign" → DM叙事模式
  ├─ play_style="dice_assistant" → 骰娘检定模式
  └─ play_style="lobby" → 游戏外管理

统一入口: services.py → resolve_chat(mode="lobby|dm|dice")
  └─ prompt_builder.py → build_system_prompt() 7个可组合模块
    └─ llm_loop.py → execute_llm_with_tools() LLM→工具→handler循环
```

## 三种模式

| 模式 | play_style | 需要当前战役 | 行为 |
|------|-----------|------------|------|
| 游戏外 | `lobby` | 可选 | 管理战役、车卡、设定 |
| DM | `campaign` | 必须 | AI叙事、扮演NPC、推进剧情 |
| 骰娘 | `dice_assistant` | 必须 | 纯机械检定、结算、战斗主持 |

启动无战役时直接进入 lobby。`/进入DM` `/进入骰娘` 进入游戏模式，`/退出` 返回 lobby。

## 核心架构变更

### 1. LLM Agent 工具架构（取代关键词匹配）
- 全部 53+ 个工具注册在 `tools/command_tools.py` TOOL_HANDLERS
- 所有消息走 `execute_llm_with_tools()` → LLM 理解意图 → 调用工具
- `/slash` 命令保留为快速通道（不走 LLM）
- NATURAL_COMMANDS 已删除（LLM 自行理解自然语言）

### 2. 统一 Prompt 构建（取代 3 处重复）
- `prompt_builder.py` — build_system_prompt() 入口
- 7 个可组合模块：base_role, combat_awareness, combat_output, hot_data, attachment_info, turn_based, pending
- 删了 services.py 110 行内联 prompt + dice_assistant.py 重复 + message_router.py 58 行战斗 prompt

### 3. 热数据层（HotSnapshot）
- `tools/hot_character.py` — get_hot_character() 实时计算含 buff/debuff 的机械快照
- 所有投骰走 `checked_roll()`: random.randint() + DiceAuditLog 审计
- HP 变更写入 CharacterChange 表（支持撤销）
- HotSnapshot 按需注入（仅消息含机械关键词时）

### 4. 战斗系统 v3.0 — 回合动作配额
- 回合不自动推进，动作配额追踪：main_action, bonus_action, extra_actions, reaction, movement
- `end_turn` 工具才推进回合
- `use_feature("action_surge")` 获得额外动作
- `ask_clarification` 追问不消耗动作
- 骰娘自由战斗（真人 DM 管回合）vs 系统回合制

### 5. Lobby 模式 — LLM 通过 lobby_state 自由控制流程
- `tools/lobby_tools.py` — 4 个状态工具
- `campaign.config.lobby_state`: {dm_confirmed, generated_setting, pending_options}
- LLM 读状态 → 判断流程 → 写状态 → 执行操作
- 不再有硬编码的 pending 流程

### 6. Memory 管理 & Context 压缩
- `memory_compressor.py` — 每 20 条事件自动触发 LLM 摘要压缩
- Memory 分层：hot(最近) → warm(已压缩) → archived
- 压缩走 subagent 后台异步（不阻塞用户消息）
- 修复 context_prompt 注入两次的 bug

### 7. Subagent 系统 (6 个角色)
- `campaign_setting_reviewer` — 审核设定草稿
- `character_sheet_reviewer` — 审核角色卡
- `character_sheet_completer` — 补全装备/背景/外貌 (focus: equipment/backstory/appearance)
- `content_writer` — LLM 生成 NPC/设定/quest (generate_content tool, 11 种 type)
- `plan_runner` — 协调多步计划 (execute_plan tool, 同步执行)
- `campaign_compressor` — 自动事件压缩

### 8. Plan Runner
- `execute_plan` 工具 — LLM 生成 steps JSON 数组
- 依赖拓扑排序，协调者占 1 个 worker
- 每个 step 同步执行（不创建子 subagent）
- 用途：完整车卡+细化+导出

### 9. 文件处理
- `parse_character_sheet_xlsx()` — 读取 D&D 5E 人物卡 xlsx
- .xlsx 解析器注册（检测人物卡模板 → 结构化导入）
- 附件持久化（campaign.config.last_attachments，最近 5 个）
- QQ 导出后 @用户通知
- 骰子被动监听（发 `2d6+3` 无 @也自动投骰）

## 关键文件地图

| 文件 | 作用 |
|------|------|
| `main.py` | FastAPI + NapCat callback |
| `services.py` | 统一 resolve_chat(lobby/DM/dice) |
| `message_router.py` | 消息路由 + 后台任务通知注入 |
| `prompt_builder.py` | 统一系统 prompt 构建 |
| `llm_loop.py` | LLM → tool_call → handler 循环 |
| `tools/command_tools.py` | 53+ 工具 schema + handler 注册 |
| `tools/combat_tools.py` | 战斗行动 + use_feature/end_turn |
| `tools/check_tools.py` | 检定/伤害/治疗/撤销/条件 |
| `tools/hot_character.py` | HotSnapshot + checked_roll + CharacterChange |
| `tools/lobby_tools.py` | Lobby 状态管理工具 |
| `tools/character_builder.py` | 人物卡构建 + xlsx 导入导出 |
| `dice_assistant.py` | 骰娘快速路径 + DM确认 |
| `campaign_control.py` | execute_command + DM 命令 |
| `campaign_turns.py` | 回合管理 + 动作配额 |
| `subagent_runner.py` | 6 个后台 subagent 角色 |
| `memory_compressor.py` | 事件 LLM 压缩 |
| `integrations/napcat.py` | NapCat HTTP + 文件上传 + 多 self_id |
| `db/models.py` | 数据模型 (含 DiceAuditLog, CharacterChange) |

## 配置要点

```env
NAPCAT_SELF_ID=1534055688,3420483665   # 两个 bot 账号
NAPCAT_DM_USER_IDS=2480933622          # DM QQ
DEEPSEEK_API_KEY=sk-...                # LLM API
LLM_MODEL=deepseek-v4-flash
```

群聊仅 `903107519` 可用。`napcat_require_group_at=true`（群聊必须 @bot）。

## 测试状态

38 passed, 19 failed（失败均来自旧阻塞模式移除，与当前架构无关）。

## 数据库

SQLite: `data/dm_agent.db`。清空后仅 `rule_chunks: 510`。
