"""
战斗展示函数库：先攻表、战斗态势
"""

def build_combat_table(combatants):
    """
    生成战斗态势 Markdown 表格
    
    参数:
        combatants: list[dict] - 参战单位列表，每个包含:
            - name: str
            - emoji: str
            - initiative: int
            - hp: int
            - maxHp: int
            - ac: int
            - position: str
            - status: str (等待/行动中/已行动/死亡)
    
    返回:
        str: Markdown 格式先攻表
    """
    sorted_units = sorted(combatants, key=lambda x: x['initiative'], reverse=True)
    
    rows = []
    for i, unit in enumerate(sorted_units, 1):
        # 死亡单位名称划掉
        if unit['status'] == '死亡':
            name_display = f"~~{unit['emoji']} {unit['name']}~~"
            hp_display = f"~~{unit['hp']}~~"
        else:
            name_display = f"{unit['emoji']} **{unit['name']}**"
            hp_display = f"**{unit['hp']}/{unit['maxHp']}**"
        
        status_map = {
            '已行动': '✅已行动',
            '行动中': '⏳**行动中**',
            '死亡': '💀死亡',
            '等待': '⏳等待',
        }
        status_mark = status_map.get(unit['status'], '⏳等待')
        
        rows.append(
            f"| **{i}** | {name_display} | **{unit['initiative']}** | "
            f"{hp_display} | **{unit['ac']}** | {unit['position']} | {status_mark} |"
        )
    
    header = "| 顺序 | 角色/怪物 | 先攻 | HP | AC | 位置 | 备注 |\n|:---:|-----------|:----:|:--:|:--:|:--------:|------|"
    return header + "\n" + "\n".join(rows)


def build_init_quick_table(combatant_names):
    """
    快速初始化先攻表（战斗开始时使用）
    
    参数:
        combatant_names: list[str] - 参战者名称列表
    
    返回:
        str: 待填充的先攻表框架
    """
    return build_combat_table([
        {"name": n, "emoji": "", "initiative": 0, 
         "hp": "?", "maxHp": "?", "ac": "?", 
         "position": "?", "status": "等待"}
        for n in combatant_names
    ])
