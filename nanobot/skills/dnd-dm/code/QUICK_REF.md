```
import os, json, re, random
from datetime import datetime

# ===== dice/rolls.py =====
roll_d20(advantage=None)                     # 投 d20（普通/优势/劣势）→ {result, detail, type}
roll_dice(dice_spec)                          # 投指定骰子 "d6" → int
rolling(expr)                                 # 通用表达式 "3d6+2" → {result, detail, rolls}
roll_stat()                                   # 4d6去最低 → int

# ===== combat/display.py =====
build_combat_table(combatants)               # 生成先攻表Markdown → str
build_init_quick_table(names)                # 快速初始化先攻表 → str

# ===== combat/resolve.py =====
check_hit(d20_face, bonus, ac)               # 命中判断 → {hit, critical, detail}
calc_damage(count, sides, bonus, extra, crit) # 伤害结算 → {total, rolls, detail}
calc_save_dc(base_dc, bonuses)               # 计算豁免DC → int

# ===== save/io.py =====
list_saves()                                  # 扫描存档 → [{num, timestamp, chapter, ...}]
load_save(save_num)                           # 载入存档 → dict | None
write_save(data)                              # 自动编号存档 → str (filename)
get_save_display(saves)                      # 获取展示文本 → str

# ===== module/scanner.py =====
scan_modules()                                # 扫描模组 → [{name, chapters, ...}]
read_chapter(module_name, num)                # 读取章节 → {content, filename}
build_module_structure(module_name)           # 获取结构信息 → dict

# ===== module/builder.py =====
build_module_structure_files(name, out_dir)  # 生成结构文件 → [file_paths]

# ===== party/live.py =====
get_all_characters()                          # 全部角色 → dict
get_character(name)                           # 单个角色 → dict | None
update_party(data)                            # 覆盖更新 → bool
update_character(name, updates)               # 更新字段 → bool
update_hp(name, new_hp)                       # 更新HP → bool
update_spell_slots(name, level, used)         # 更新法术位 → bool
find_item_in_party(item_name)                 # 搜物品 → [{character, item, equipped}]
get_party_summary()                            # 队伍摘要 → str

# ===== party/xp.py =====
calc_combat_xp(cr_list, level, size=4)        # 战斗经验计算 → {total_xp, per_person}
calc_noncombat_xp(difficulty, level, size=4)  # 非战斗经验 → {per_person}
get_level_up_xp_requirement(level)            # 升级所需XP → int
```
