"""
存档格式模板 —— 预定义完整存档结构，避免 LLM 每次构建
"""
from datetime import datetime

def new_save_template(party_data, chapter, location, timestamp=None, recent_events=None):
    """
    生成符合规则14的完整存档结构，含剧情摘要字段
    
    参数:
        party_data: list[dict] - 队伍角色列表
        chapter: int - 当前章节
        location: str - 当前位置
        timestamp: str - 可选，默认当前时间
        recent_events: list[str] - 最近事件（可选，用于剧情摘要）
    
    返回:
        dict: 完整存档数据
    """
    result = {
        "timestamp": timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "dmRulesVersion": "1.0.6",
        "echoEnabled": False,  # 由规则20:回声系统，启动游戏时由玩家决定
        "chapter": chapter,
        "location": location,
        "party": _format_party(party_data),
        "mainQuests": [],
        "sideQuests": [],
        "completedNodes": [],
        "logEntries": [],
        "notes": [],
        "plotSummary": None,  # 由 write_save_with_summary() 填充
    }
    return result


def _format_party(party_data):
    """确保每个角色符合规则14.1的字段要求"""
    formatted = []
    for char in party_data:
        formatted.append({
            # 基础信息
            "name": char.get('name', ''),
            "race": char.get('race', ''),
            "class": char.get('class', ''),
            "level": char.get('level', 1),
            "subclass": char.get('subclass', ''),
            "background": char.get('background', ''),
            
            # 属性
            "stats": {
                "strength": char.get('stats', {}).get('strength', 10),
                "dexterity": char.get('stats', {}).get('dexterity', 10),
                "constitution": char.get('stats', {}).get('constitution', 10),
                "intelligence": char.get('stats', {}).get('intelligence', 10),
                "wisdom": char.get('stats', {}).get('wisdom', 10),
                "charisma": char.get('stats', {}).get('charisma', 10),
            },
            
            # 战斗
            "hp": char.get('hp', 10),
            "maxHp": char.get('maxHp', 10),
            "tempHp": char.get('tempHp', 0),
            "ac": char.get('ac', 10),
            "speed": char.get('speed', 30),
            "initiative": char.get('initiative', 0),
            "proficiencyBonus": char.get('proficiencyBonus', 2),
            "passivePerception": char.get('passivePerception', 10),
            
            # 技能熟练
            "skills": char.get('skills', {}),
            "savingThrows": char.get('savingThrows', {}),
            
            # 装备与物品
            "equipment": char.get('equipment', []),
            "inventory": char.get('inventory', []),
            "gold": char.get('gold', 0),
            
            # 法术
            "spellSlots": char.get('spellSlots', []),
            "spells": char.get('spells', []),
            "cantrips": char.get('cantrips', []),
            "spellcastingAbility": char.get('spellcastingAbility', ''),
            "spellSaveDC": char.get('spellSaveDC', 10),
            "spellAttackBonus": char.get('spellAttackBonus', 2),
            
            # 职业特性
            "classFeatures": char.get('classFeatures', []),
            "feats": char.get('feats', []),
            "racialTraits": char.get('racialTraits', []),
            "backgroundFeatures": char.get('backgroundFeatures', []),
            
            # 其他
            "proficiencies": char.get('proficiencies', []),
            "languages": char.get('languages', []),
            "xp": char.get('xp', 0),
            "hd": char.get('hd', "d8"),
            "hdUsed": char.get('hdUsed', 0),
            "inspiration": char.get('inspiration', False),
            
            # 外貌/设定
            "emoji": char.get('emoji', '🧝'),
            "alignment": char.get('alignment', '中立善良'),
            "description": char.get('description', ''),
        })
    return formatted


def new_quest_template(quest_id, name, quest_type, description):
    """
    生成任务条目
    
    参数:
        quest_id: str - 任务ID
        name: str - 任务名
        quest_type: str - 'main' 或 'side'
        description: str - 任务描述
    
    返回:
        dict: 任务条目
    """
    return {
        "id": quest_id,
        "name": name,
        "type": quest_type,
        "status": "进行中",
        "description": description,
        "steps": [],
        "reward": "",
    }


def new_character_template(name, char_class, level=1):
    """
    生成新的角色数据模板（用于建卡）
    
    返回:
        dict: 角色初始数据
    """
    class_hd = {
        "战士": "d10", "圣武士": "d10", "游侠": "d10",
        "野蛮人": "d12", "武僧": "d8",
        "法师": "d6", "术士": "d6", "邪术师": "d8",
        "牧师": "d8", "德鲁伊": "d8", "吟游诗人": "d8",
        "游荡者": "d8", "契术师": "d8",
    }
    return {
        "name": name,
        "class": char_class,
        "level": level,
        "hp": 10, "maxHp": 10,
        "ac": 10, "speed": 30,
        "proficiencyBonus": 2,
        "stats": {"strength": 10, "dexterity": 10, "constitution": 10,
                   "intelligence": 10, "wisdom": 10, "charisma": 10},
        "skills": {},
        "equipment": [],
        "inventory": [],
        "spellSlots": [],
        "spells": [],
        "cantrips": [],
        "classFeatures": [],
        "feats": [],
        "hd": class_hd.get(char_class, "d8"),
        "hdUsed": 0,
        "xp": 0,
    }
