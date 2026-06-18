# 代码模板库索引

本库收录城主系统在运行过程中积累的代码模板。每次通过自然语言生成的代码经过函数化改造后登记在此。

## 使用规则

1. **优先检索**：当需要实现某个功能时，先在本索引中搜索，找到对应函数后直接调用
2. **无匹配再生成**：检索不到合适的函数时，通过大模型生成，并将新代码登记入库
3. **函数化**：所有代码必须封装为函数，有明确的输入参数和返回值
4. **文档化**：每个函数必须有说明文档（参数、返回值、调用示例）

## Token优化强制规则

- **场景加载**：章节过渡后必须调用 `module/scene_index.py:build_scene_index()` 生成场景索引，禁止整章加载
- **世界状态**：每次派系/任务/NPC变更必须调用 `state/world.py:update_faction()` / `update_quest()` / `update_npc_status()`，替代纯文本描述
- **剧情摘要**：每次存档必须调用 `summary/generate.py:generate_plot_summary()` 生成摘要，替代完整聊天历史
- **规则检索**：需要特定规则时先用 `rule/retriever.py:retrieve_by_keyword()` 确定规则号，再按号查DM_RULES.md

### 检定计算强制规则

- 所有检定（技能/攻击/豁免）必须使用 `checks.py` 的 ⭐ 标注函数来展示结果
- 这些函数强制展示 **每项加值明细**（熟练加值、属性调整、装备加成等逐项列出）
- 禁止使用 `resolve.py` 的 `check_hit()` 直接展示给玩家——它不展示加值明细
- `resolve.py` 的 `check_hit()` 仅限内部计算使用

---

## 分类索引

### 🎲 骰子 / dice

| 函数 | 文件 | 说明 |
|------|------|------|
| `roll_d20(advantage=None)` | [dice/rolls.py](dice/rolls.py) | 投 d20，支持通常/优势/劣势 |
| `rolling(expr)` | [dice/rolls.py](dice/rolls.py) | 通用骰子表达式求值，如 `3d6+2`、`d20+5` |

### ⚔️ 战斗 / combat

| 函数 | 文件 | 说明 |
|------|------|------|
| `build_combat_table(combatants)` | [combat/display.py](combat/display.py) | 生成先攻+战斗态势 Markdown 表格 |
| `check_hit(attack_roll_total, attacker_bonus, defender_ac)` | [combat/resolve.py](combat/resolve.py) | 判断攻击是否命中（内部计算用，不展示加值明细） |
| `calc_damage(attacker, hit_result)` | [combat/resolve.py](combat/resolve.py) | 结算伤害，计入装备加值和武器骰 |
| ⭐ `resolve_skill_check(d20_result, components, dc, skill_name, character_name, advantage)` | [combat/checks.py](combat/checks.py) | **检定主函数**：全公式展示（强制使用），d20+每项加值明细→合计→成功/失败 |
| ⭐ `check_hit_v2(d20_result, components, defender_ac, attacker_name, weapon_name)` | [combat/checks.py](combat/checks.py) | **攻击命中展示**（强制使用）：全公式展示，同 resolve_skill_check 但输出转为命中判断 |
| ⭐ `resolve_save_check(d20_result, components, dc, save_name, character_name)` | [combat/checks.py](combat/checks.py) | **豁免检定展示**（强制使用）：全公式展示 |

### 💾 存档 / save

| 函数 | 文件 | 说明 |
|------|------|------|
| `list_saves()` | [save/io.py](save/io.py) | 扫描 saves/ 目录，返回所有存档列表（含时间戳、编号） |
| `load_save(save_num)` | [save/io.py](save/io.py) | 载入指定编号的存档 |
| `write_save(save_data)` | [save/io.py](save/io.py) | 自动编号写入新存档 |

### 📦 模组 / module

| 函数 | 文件 | 说明 |
|------|------|------|
| `scan_modules()` | [module/scanner.py](module/scanner.py) | **通用**：扫描 modules/ 目录，自动识别所有模组（按文件名前缀分组） |
| `parse_chapters(module_name)` | [module/scanner.py](module/scanner.py) | **通用**：解析指定模组的所有章节文件和结构 |
| `load_chapter(module, chapter)` | [module/scanner.py](module/scanner.py) | **通用**：加载指定章节文件内容 |
| `build_module_structure(module_name)` | [module/builder.py](module/builder.py) | **通用**：构建 MODULE_STRUCTURE/MODULE_NODES 等文件 |
| ⭐ `init_module(module_name)` | [module/init.py](module/init.py) | **新模组初始化**：自动生成 MODULE_INDEX.md / MODULE_ARC.md / world_state.json / 场景索引（不限模组） |
| ⭐ `scan_chapter_structure(filepath)` | [module/builder.py](module/builder.py) | **通用**：扫描单章文件的结构（场景/遭遇/NPC） |
| ⭐ `build_chapter_content(chapter_filepath, chapter_label, module_name)` | [module/builder.py](module/builder.py) | **通用章节过渡**：接受任意文件路径，自动识别模块名并建立场景索引（不限模组） |
| ⭐ `build_scene_index(filepath)` | [module/scene_index.py](module/scene_index.py) | **通用场景懒加载**：扫描章节文件按`##`标题划分场景，记录起止行号和关键词标签（中英文通用） |
| ⭐ `save_scene_index(module_name, index)` | [module/scene_index.py](module/scene_index.py) | 保存场景索引到 `srd/scenes_index.json`（支持多模组共存） |
| ⭐ `load_scene_by_index()` | [module/scene_index.py](module/scene_index.py) | 从索引加载当前场景的原文行（通过 `_current_module_file` 定位） |
| ⭐ `set_current_scene(current_file_key, scene_title)` | [module/scene_index.py](module/scene_index.py) | 切换当前场景（通用：接受文件key+场景名） |

### 🌍 世界状态 / state

| 函数 | 文件 | 说明 |
|------|------|------|
| ⭐ `load_world_state()` | [state/world.py](state/world.py) | **每次对话加载**：读取 world_state.json，返回结构化世界状态 |
| ⭐ `save_world_state(state)` | [state/world.py](state/world.py) | 写入 world_state.json |
| ⭐ `update_faction(world, faction, delta, note)` | [state/world.py](state/world.py) | 更新派系关系（传入变更原因），自动生成关系摘要 |
| ⭐ `update_quest(world, quest, status)` | [state/world.py](state/world.py) | 更新任务状态（完成/进行中/待触发/已失败） |
| ⭐ `update_npc_status(world, npc, status)` | [state/world.py](state/world.py) | 更新关键NPC状态 |
| ⭐ `get_world_summary(world)` | [state/world.py](state/world.py) | **强制使用**：生成50-100 token的世界状态摘要 |
| `discover_location(world, location)` | [state/world.py](state/world.py) | 记录发现新地点 |

### 📝 剧情摘要 / summary

| 函数 | 文件 | 说明 |
|------|------|------|
| ⭐ `generate_plot_summary(world, npc, events, levels)` | [summary/generate.py](summary/generate.py) | **存档时强制使用**：生成100-300 token的剧情摘要 |
| ⭐ `save_summary(summary, chapter, scene)` | [summary/generate.py](summary/generate.py) | 保存剧情摘要到 `plot_summary.json` |
| ⭐ `load_summary()` | [summary/generate.py](summary/generate.py) | **读档时优先加载**：优先载入剧情摘要替代完整聊天历史 |
| `update_summary(existing, events, world, npc, levels)` | [summary/generate.py](summary/generate.py) | 增量更新摘要（保留核心+追加最新事件） |

### 🔍 规则检索 / rule

| 函数 | 文件 | 说明 |
|------|------|------|
| ⭐ `retrieve_by_keyword(keyword)` | [rule/retriever.py](rule/retriever.py) | **规则按需检索**：关键词→规则号列表，替代加载整层规则 |
| ⭐ `extract_rule_text(dm_rules_path, rule_id)` | [rule/retriever.py](rule/retriever.py) | 从DM_RULES.md提取指定规则全文 |
| ⭐ `load_rules_by_keywords(dm_rules_path, keyword)` | [rule/retriever.py](rule/retriever.py) | 一键操作：关键词→匹配规则全文列表 |

### 👥 角色 / party

| 函数 | 文件 | 说明 |
|------|------|------|
| `update_party(party_data)` | [party/live.py](party/live.py) | 更新 live_party.json 中的角色状态 |
| `get_character(name)` | [party/live.py](party/live.py) | 从 live_party.json 获取单个角色数据 |
| `get_all_characters()` | [party/live.py](party/live.py) | 从 live_party.json 获取全部角色数据 |
| `find_item_in_party(item_name)` | [party/live.py](party/live.py) | 搜索物品在哪个角色身上 |
| `calc_xp(combatants, mode)` | [party/xp.py](party/xp.py) | 计算经验值（战斗/非战斗结算） |

### 🌊 回声映射 / echo

| 函数 | 文件 | 说明 |
|------|------|------|
| `extract_mood(text)` | [echo/echo_generator.py](echo/echo_generator.py) | 提取文本中的情绪标签 |
| `extract_themes(text)` | [echo/echo_generator.py](echo/echo_generator.py) | 提取文本中的主题类别 |
| `map_to_quest(text, player_name)` | [echo/echo_generator.py](echo/echo_generator.py) | 玩家倾诉 → 任务骨架 |
| `save_to_journal(quest)` | [echo/echo_generator.py](echo/echo_generator.py) | 保存任务到 themes.json |

---

## 目录结构

```
code/
├── dice/           → 骰子与随机数
│   └── rolls.py
├── combat/         → 战斗结算与展示
│   ├── display.py
│   └── resolve.py
├── save/           → 存档读写
│   └── io.py
├── module/         → 模组加载与解析
│   ├── scanner.py
│   └── builder.py
├── party/          → 角色状态与经验
│   ├── live.py
│   └── xp.py
└── echo/           → 回声映射（现实→奇幻）
    ├── echo_generator.py
    ├── mapper_rules.md
    └── themes.json
```

---

## 新增代码模板流程

1. 将代码封装为函数（def func_name(params) → return value）
2. 确认函数名、参数、返回值、说明文档完整
3. 放入对应的 `code/<category>/` 目录
4. 在本索引 CODE_LIBRARY.md 中添加条目
5. 提交到版本记录
