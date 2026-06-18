"""
战斗结算公式模板 —— 预定义格式字符串，减少 LLM 构造输出
"""
from combat.resolve import check_hit, calc_damage, calc_save_dc

# ===== 攻击检定公式 =====
def attack_check(attacker_name, d20_face, attack_bonus, target_name, target_ac):
    """
    攻击检定全流程（预定义格式）
    
    返回:
        str: 可直接输出的文本
    """
    result = check_hit(d20_face, attack_bonus, target_ac)
    
    text = f"⚔️ **{attacker_name}** 攻击 **{target_name}**\n"
    text += f"  🎲 攻击检定: d20={d20_face} + {attack_bonus} = **{result['total']}** vs AC {target_ac}\n"
    
    if result['critical']:
        text += f"  ⚡ **重击！** 伤害骰翻倍！\n"
    elif result['hit']:
        text += f"  ✅ **命中！**\n"
    else:
        text += f"  ❌ **未命中**\n"
    
    return text


def damage_calc_text(attacker_name, dice_count, dice_sides, bonus=0, extra_dice=None, critical=False):
    """伤害结算文本"""
    result = calc_damage(dice_count, dice_sides, bonus, extra_dice, critical)
    
    text = f"  💥 **伤害**: {result['detail']}"
    return text


def save_check_text(target_name, save_type, dc, d20_face, save_bonus):
    """
    豁免检定文本
    
    参数:
        save_type: str - '力量'/'敏捷'/'体质'/'智力'/'感知'/'魅力'
    """
    total = d20_face + save_bonus
    passed = total >= dc
    
    text = f"🛡️ **{target_name}** {save_type}豁免\n"
    text += f"  🎲 d20={d20_face} + {save_bonus} = **{total}** vs DC {dc}\n"
    text += f"  {'✅ 豁免成功' if passed else '❌ 豁免失败'}"
    
    return text


def spell_attack_text(caster_name, spell_name, d20_face, attack_bonus, target_name, target_ac):
    """法术攻击检定"""
    return attack_check(caster_name, d20_face, attack_bonus, target_name, target_ac).replace(
        "⚔️", "🔮")


def full_attack_sequence(attacker_name, target_name, d20_face, attack_bonus, target_ac,
                         dice_count, dice_sides, damage_bonus=0, extra_dice=None, critical=False):
    """
    完整的攻击序列（攻击检定 + 伤害结算）
    
    返回:
        str: 完整输出文本
    """
    hit_result = check_hit(d20_face, attack_bonus, target_ac)
    
    lines = [
        f"⚔️ **{attacker_name}** → **{target_name}**",
        f"  攻击: d20={d20_face} + {attack_bonus} = {hit_result['total']} vs AC {target_ac}",
    ]
    
    if hit_result['critical']:
        critical = True
        lines.append("  ⚡ **重击！**")
    
    if hit_result['hit'] or hit_result['critical']:
        dmg = calc_damage(dice_count, dice_sides, damage_bonus, extra_dice, critical)
        lines.append(f"  伤害: {dmg['detail']}")
    else:
        lines.append("  ❌ 未命中")
    
    return "\n".join(lines)


# ===== 法术DC =====
def spell_dc_text(caster_name, spell_name, base_dc, bonuses=None, extra=""):
    """法术 DC 文本"""
    dc = calc_save_dc(base_dc, bonuses)
    return f"🔮 **{caster_name}** 释法「{spell_name}」豁免 DC **{dc}** {extra}"


# ===== 战斗轮次 =====
def round_header(round_num, combat_name, location):
    """回合标题"""
    return f"━━━ ⚔️ 第 {round_num} 轮 — {combat_name}（{location}）━━━"


def turn_indicator(unit_name, status="行动中"):
    """行动指示"""
    markers = {"行动中": "⏳", "已行动": "✅", "等待": "○", "死亡": "💀"}
    m = markers.get(status, "○")
    return f"{m} **{unit_name}** {'行动中' if status == '行动中' else status}"


def hp_change_text(unit_name, old_hp, new_hp, max_hp):
    """HP 变更摘要"""
    bar_len = 10
    filled = int(new_hp / max_hp * bar_len) if max_hp > 0 else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    return f"{unit_name}: {bar} {new_hp}/{max_hp} ({old_hp}→{new_hp})"


# ===== 环境/条件 =====
def environment_text(terrain, light, special=""):
    """环境描述"""
    parts = [f"地形: {terrain}", f"光线: {light}"]
    if special:
        parts.append(f"特殊: {special}")
    return " | ".join(parts)


def condition_text(unit_name, effects):
    """状态效果列表"""
    if not effects:
        return f"{unit_name}: 无特殊状态"
    return f"{unit_name}: " + ", ".join(f"[{e['name']}] {e['remaining']}轮" for e in effects)
