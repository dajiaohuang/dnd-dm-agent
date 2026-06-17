# 战斗系统

## 两种战斗模式

| | 系统回合制战斗 | 骰娘自由战斗 |
|---|---|---|
| 管理者 | 系统 | 真人 DM |
| 进入方式 | `/进入战斗` (DM确认后) | 自然发生 |
| 先攻 | 系统投+排序 | 真人 DM 管 |
| 回合推进 | `end_turn` 工具 | 不自动推进 |
| 动作配额 | ✅ 追踪消耗 | ♾️ 不限 |
| 常用场景 | 自动化战斗 | 真人团 @bot 辅助 |

### 骰娘自由战斗（主流）

真人 DM 主持战斗，玩家在群聊中自由 @bot 进行行动。骰娘只负责：读热数据 → 投骰 → 结算 → 写回。

```
@bot "帮Aric、Goblin、Mira扔先攻"
  → 手动投每人先攻 → 输出列表

@bot "我用长剑攻击地精"
  → combat_attack → checked_roll → "命中8点"

@bot "等等上次伤害记错了"
  → undo_damage → CharacterChange反推 → HP恢复

@bot "喝治疗药水"
  → apply_healing → HP + 2d4+2
```

骰娘根据对话历史感知回合，提醒跳过或不一致。DM 说 "进入战斗" 并确认后才进入系统管理。

### 系统回合制战斗

系统管理先攻、回合、动作配额。

**启动流程:**

```
DM: /进入战斗 或 "进入战斗"
骰娘: "准备进入。需要确认:
       1. 哪些角色参战？
       2. 有没有先攻优势/劣势？"
DM: "Aric、Goblin、Mira。Aric先攻优势"
骰娘: 投全体先攻 → 排序 → 进入 turn_based
```

## 回合动作配额

每个回合系统追踪剩余配额，不自动推进:

```
轮到卡利恩 (Fighter Lv5) → 配额: 主动作1 附赠1 反应1 移动30

"长剑攻击地精"
  → combat_attack → main_action: 1→0
  → "命中8点。剩余: 附赠1 移动30"

"动作如潮"
  → use_feature("action_surge") → extra_actions: 0→1
  → "动作如潮发动！剩余: 主动作1 附赠1 移动30"

"再打兽人"
  → combat_attack → extra_actions: 1→0
  → "命中12点。剩余: 附赠1 移动30"

"副手短剑砍地精"
  → combat_attack(use_bonus_action=True) → bonus_action: 1→0
  → "命中4点。剩余: 移动30"

"结束回合"
  → end_turn → advance_turn → "轮到 Goblin"
```

### 配额规则

| 主动作 (main_action) | 攻击、施法、疾走、撤退、闪避、检定 |
| 附赠动作 (bonus_action) | 双武器副手、灵巧施法、回气、狂暴 |
| 额外动作 (extra_actions) | 动作如潮获得 |
| 反应 (reaction) | 借机攻击、护盾术 (回合外也可用) |
| 移动 (movement) | 尺数 |

### 追问机制

LLM 不猜测用户意图，信息不足时使用 LLM 的自然语言追问。

```
"我用法术攻击他"
  → LLM: "用哪个法术？你准备了魔法飞弹(1环)和火球术(3环)"

"火球术"
  → LLM: "用几环？你还有1个3环和2个4环"

"3环"
  → LLM: "目标是谁？"

"兽人和地精"
  → combat_cast_spell(spell="火球术", level=3, targets=["兽人","地精"], save_type="dex")
  → main_action: 1→0, spell_slots[3]: 1→0
  → "火球术 DC15。伤害 8d6 = 32"
```

## 战斗工具

### 行动工具

| 工具 | 消耗 | 自动结算 |
|------|------|---------|
| `combat_attack` | main/bonus/extra | d20+加值 → 攻击骰; 伤害骰 |
| `combat_cast_spell` | main/bonus/extra | 法术DC/攻击+豁免 |
| `combat_ability_check` | main | d20+调整值 (推撞/擒抱) |
| `combat_dash` | main | 速度翻倍 |
| `combat_disengage` | main | 免借机攻击 |
| `combat_dodge` | main | 攻击劣势, 敏捷豁免优势 |
| `use_feature` | 特性决定 | 动作如潮/回气/狂暴 |
| `end_turn` | — | 推进回合 |
| `turn_status` | — | 查询剩余配额 |

### 检定工具

| 工具 | 说明 |
|------|------|
| `ability_check` | 属性/技能检定 (读 HotSnapshot) |
| `saving_throw` | 豁免检定 |
| `apply_damage` | 造成伤害 → HP 写入 |
| `apply_healing` | 治疗 → HP 写入 |
| `apply_condition` | 添加状态 |
| `remove_condition` | 移除状态 |
| `undo_damage` | 撤销最近伤害 (CharacterChange反推) |
| `undo_healing` | 撤销最近治疗 |
| `recent_changes` | HP 变更历史 |

## 热数据层

每次战斗行动读取 `get_hot_character()` 返回实时 HotSnapshot:

```
base character data (characters 表)
  + active_effects (buff/debuff/装备/法术效果)
  = HotSnapshot {
      abilities: {str: {score:18, mod:+4}, ...}
      armor_class: 18, current_hp: 28, max_hp: 28
      saving_throws: {str: +7, dex: +2, ...}
      skills: {athletics: {bonus: +7}, ...}
      attacks: [{name:"长剑", bonus:+7, damage:"1d8+4"}]
      spell_dc: 15, spell_attack_bonus: 7
      conditions: ["poisoned"]
    }
```

所有投骰走 `checked_roll()`: `random.randint()` + `DiceAuditLog` 审计。
HP 变更写入 `CharacterChange` 表 (before/after 对比)，支持精确撤销。

## DM 模式 vs 骰娘模式

两者共用同一战斗管线，区别仅在输出风格:

| | DM 战斗 | 骰娘战斗 |
|---|---------|---------|
| 输出 | 机械数据 → LLM 叙事包装 | 纯机械数据 |
| 扮演 | ✅ NPC台词/环境描写 | ❌ `strict_tool_output` 过滤 |
| 建议 | ✅ 战术建议(默认) | ❌ 禁止(默认) |
| 温度 | 0.7 | 0.2 |
| 底层路径 | `resolve_chat(mode="dm")` | `resolve_chat(mode="dice")` |
