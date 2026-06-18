"""
战斗状态跟踪器（JSON持久化）—— 代替 LLM 记忆战斗状态
"""
import json
import os
from datetime import datetime

COMBAT_FILE = 'combat_state.json'

def new_combat(combat_name, location, combatants):
    """
    初始化新战斗
    
    参数:
        combat_name: str - 战斗名称
        location: str - 战斗位置
        combatants: list[dict] - 参战者数据
    
    返回:
        dict: 完整战斗状态
    """
    from combat.display import build_combat_table
    
    # 按先攻排序
    sorted_units = sorted(combatants, key=lambda x: x.get('initiative', 0), reverse=True)
    
    state = {
        "combat_name": combat_name,
        "location": location,
        "round": 1,
        "current_turn": 0,  # 当前行动者在 sorted_units 中的索引
        "is_active": True,
        "started_at": datetime.now().isoformat(),
        "units": sorted_units,
        "log": [f"⚔️ {combat_name} 开始！位置：{location}"],
        "environment": {"terrain": "", "light": "", "special": ""},
    }
    
    save_combat(state)
    return state


def save_combat(state):
    """持久化战斗状态到文件"""
    with open(COMBAT_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_combat():
    """
    加载当前战斗状态
    
    返回:
        dict: 战斗状态，或 None
    """
    if not os.path.exists(COMBAT_FILE):
        return None
    try:
        with open(COMBAT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def get_current_turn_unit(state):
    """
    获取当前行动者
    
    返回:
        dict: 当前行动单位
    """
    idx = state['current_turn']
    if idx < len(state['units']):
        return state['units'][idx]
    return None


def advance_turn(state):
    """
    前进到下一个行动者。所有人行动完后进入下一轮。
    
    返回:
        dict: 更新后的状态
    """
    state['current_turn'] += 1
    
    # 跳过死亡的
    while state['current_turn'] < len(state['units']):
        if state['units'][state['current_turn']].get('status') != '死亡':
            break
        state['current_turn'] += 1
    
    # 本轮结束 → 进入下一轮
    if state['current_turn'] >= len(state['units']):
        state['round'] += 1
        state['current_turn'] = 0
        # 重置所有活着的单位为"等待"
        for unit in state['units']:
            if unit.get('status') != '死亡':
                unit['status'] = '等待'
        state['log'].append(f"--- 第 {state['round']} 轮 ---")
    
    save_combat(state)
    return state


def apply_damage(state, unit_name, damage, source=""):
    """
    应用伤害
    
    参数:
        unit_name: str - 目标名字
        damage: int - 伤害值
        source: str - 伤害来源
    
    返回:
        dict: 更新后的单元数据
    """
    unit = _find_unit(state, unit_name)
    if not unit:
        return None
    
    old_hp = unit['hp']
    unit['hp'] = max(0, unit['hp'] - damage)
    
    log_msg = f"{unit_name} 受到 {damage} 点伤害 ({old_hp}→{unit['hp']})"
    if source:
        log_msg += f" [来源: {source}]"
    
    if unit['hp'] == 0:
        unit['status'] = '死亡'
        log_msg += " 💀已死亡"
    
    state['log'].append(log_msg)
    save_combat(state)
    return unit


def heal_unit(state, unit_name, heal):
    """治疗"""
    unit = _find_unit(state, unit_name)
    if not unit:
        return None
    old_hp = unit['hp']
    unit['hp'] = min(unit.get('maxHp', 999), unit['hp'] + heal)
    state['log'].append(f"{unit_name} 恢复 {heal} HP ({old_hp}→{unit['hp']})")
    save_combat(state)
    return unit


def set_status(state, unit_name, status):
    """
    设置状态（等待/行动中/已行动/死亡）
    
    参数:
        unit_name: str
        status: str
    
    返回:
        bool
    """
    unit = _find_unit(state, unit_name)
    if not unit:
        return False
    unit['status'] = status
    save_combat(state)
    return True


def add_effect(state, unit_name, effect_name, duration, effect_desc):
    """添加临时状态效果"""
    unit = _find_unit(state, unit_name)
    if not unit:
        return False
    if 'effects' not in unit:
        unit['effects'] = []
    unit['effects'].append({
        "name": effect_name,
        "duration": duration,
        "remaining": duration,
        "desc": effect_desc,
    })
    state['log'].append(f"{unit_name} 获得 {effect_name}（持续{duration}回合）")
    save_combat(state)
    return True


def tick_effects(state):
    """所有单位的持续效果减1回合，过期移除"""
    for unit in state['units']:
        if unit.get('effects'):
            remaining = []
            for eff in unit['effects']:
                eff['remaining'] -= 1
                if eff['remaining'] > 0:
                    remaining.append(eff)
                else:
                    state['log'].append(f"{unit['name']} 的 {eff['name']} 已结束")
            unit['effects'] = remaining
    save_combat(state)
    return state


def end_combat(state, result):
    """
    结束战斗
    
    参数:
        result: str - 'win' / 'lose' / 'flee'
    """
    state['is_active'] = False
    state['result'] = result
    state['log'].append(f"战斗结束：{result}")
    save_combat(state)


def get_combat_table(state):
    """生成当前战斗态势表"""
    from combat.display import build_combat_table
    return build_combat_table(state['units'])


def get_combat_summary(state):
    """战斗状态摘要（短文本，低 token）"""
    living = [u for u in state['units'] if u.get('status') != '死亡']
    dead = [u for u in state['units'] if u.get('status') == '死亡']
    return (
        f"⚔️ 第{state['round']}轮 | 存活{len(living)}人 | 死亡{len(dead)}人\n"
        f"当前行动: {get_current_turn_unit(state)['name'] if get_current_turn_unit(state) else '?'}"
    )


def clear():
    """清除战斗状态"""
    if os.path.exists(COMBAT_FILE):
        os.remove(COMBAT_FILE)


def _find_unit(state, name):
    """按名字查找参战单位"""
    for unit in state['units']:
        if unit.get('name') == name:
            return unit
    return None
