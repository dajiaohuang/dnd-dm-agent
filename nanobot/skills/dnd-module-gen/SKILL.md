---
name: dnd-module-gen
description: Generate D&D 5e adventure modules. Supports one-shot adventures and sandbox campaigns. Use when the user asks to create, generate, or make a new adventure, module, or campaign setting.
---

# D&D Module Generator

Generate playable D&D 5e modules as Markdown text, then import them into the campaign database.
The generated module follows the same structure as imported PDF modules and works with the
existing `dnd_module` tool for search and scene navigation.

## Types

### One-shot（一发短团）

1 chapter, 3-5 scenes. Single session (3-6 hours).

Four templates, user picks one or random:

| Template | Scene Flow | Example |
|----------|-----------|---------|
| 经典地城 | gather → dungeon → boss | goblin cave, tomb raid |
| 调查悬疑 | crime → clues → confrontation | murder, missing person |
| 护送/救援 | mission → journey → rescue | caravan escort, hostage rescue |
| 社交博弈 | infiltrate → negotiate → betrayal | noble ball, thieves' guild |

**Output format:**

```markdown
# <模组名>

## 冒险概要
<2-3 句 hook>

## 冒险背景
<3-5 句 setting>

# <场景名1> <英文名>
<叙事/社交场景，含 NPC 列表、对话、线索>

# <场景名2> <英文名>
<地城/探索场景，含 #### 房间编号>

# <场景名3> <英文名>
<Boss/高潮场景，含敌人、战术、结局>

# <场景名4> <英文名>
<可选尾声/变数>

# 附录
## 主要 NPC
- **<名>**（<种族> <职业>）：<2-3 句>

## 怪物
- **<名>**：参见 SRD。定制：<1 句>

## 魔法物品
- **<名>**（<稀有度>）：<1-2 句>
```

**Scene requirements:**
- Every scene MUST start with `# ` (H1) heading
- Room/location headings use `#### ` (H4, gets `type: "room"`)
- Include DC values for skill checks (DC 10-15)
- Include XP/gold rewards
- NPC list with name, race, class, 1-sentence personality

### Sandbox（沙盒战役）

4-6 regions. Player-driven exploration order.

**Template:**

```markdown
# <沙盒名>

## 世界概况
<5-8 句：地图范围、核心冲突、势力格局>

# 区域<N>：<名称>
## 区域特征
<3-5 句：地形、氛围、地标>
## 势力
<谁控制这里，他们想要什么>
## 事件线：<事件名>
<触发条件、过程、结果（2-4 句）>

# 关系网
## 势力关系
| 势力A | 关系 | 势力B | 说明 |
## 随机遭遇表
| 1d6 | 遭遇 | 区域 |
|-----|------|------|

# 附录
## 关键 NPC
按区域/势力分组，每人 2-3 句
## 怪物
按区域分组
```

**Region requirements:**
- Each region has at least 1 event line and 1 faction
- Relationship table connects factions across regions
- Random encounter table has 6-12 entries

## Import

After generating, import the module text directly:

```
dnd_module action=import campaign_id=<id> module_name="<name>" content="<generated markdown>"
```

Or write to file first for large modules:

```
dnd_module action=import campaign_id=<id> module_name="<name>" source_path="<path>"
```

Then index:

```
dnd_module action=index campaign_id=<id>
```

Report: chapter count, scene count, chunk count.

## Parameters

Ask the user (or randomly generate):

- **类型**: one-shot or sandbox
- **模板** (one-shot only): dungeon / mystery / rescue / social — or random
- **主题**: e.g. undead, dragon, fey, political, planar
- **环境**: e.g. forest, desert, city, underdark, coastal
- **反派**: e.g. necromancer, dragon, cult, demon, bandit lord
- **等级范围**: e.g. 1-3, 5-7
- **特殊要求**: any additional constraints

If the user gives no parameters, randomize all and tell them what was picked.
