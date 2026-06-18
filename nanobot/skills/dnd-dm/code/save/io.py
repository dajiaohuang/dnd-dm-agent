"""
存档读写函数库
"""
import json
import os
import sys
from datetime import datetime

SAVES_DIR = 'saves'

def _ensure_saves_dir():
    """确保 saves 目录存在"""
    os.makedirs(SAVES_DIR, exist_ok=True)


def _get_next_save_num():
    """
    获取下一个可用的存档编号
    
    返回:
        int: 下一个存档编号
    """
    _ensure_saves_dir()
    max_num = 0
    if os.path.isdir(SAVES_DIR):
        for fn in os.listdir(SAVES_DIR):
            if fn.startswith('存档') and fn.endswith('.json'):
                try:
                    num = int(fn.replace('存档', '').replace('.json', ''))
                    max_num = max(max_num, num)
                except ValueError:
                    continue
    return max_num + 1


def list_saves():
    """
    扫描保存目录，返回所有存档信息
    
    返回:
        list[dict]: 每个存档包含:
            - num: int 存档编号
            - filename: str 文件名
            - timestamp: str 存档时间
            - chapter: int 章节
            - location: str 位置
            - party_levels: str 角色等级摘要
    """
    _ensure_saves_dir()
    saves = []
    if not os.path.isdir(SAVES_DIR):
        return saves
    
    for fn in sorted(os.listdir(SAVES_DIR)):
        if not (fn.startswith('存档') and fn.endswith('.json')):
            continue
        fp = os.path.join(SAVES_DIR, fn)
        try:
            with open(fp, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            
            num = int(fn.replace('存档', '').replace('.json', ''))
            party_info = ''
            if 'party' in data:
                levels = [f"{p.get('name','?')} Lv.{p.get('level',1)}" for p in data['party']]
                party_info = ', '.join(levels)
            
            saves.append({
                "num": num,
                "filename": fn,
                "timestamp": data.get('timestamp', '未知'),
                "chapter": data.get('chapter', 1),
                "location": data.get('location', '未知'),
                "party_levels": party_info,
            })
        except Exception:
            saves.append({
                "num": 0,
                "filename": fn,
                "timestamp": '损坏',
                "chapter": '?',
                "location": '无法读取',
                "party_levels": '',
            })
    
    return sorted(saves, key=lambda x: x['num'])


def load_save(save_num):
    """
    载入指定编号的存档
    
    参数:
        save_num: int - 存档编号
    
    返回:
        dict: 存档数据，或 None（不存在/损坏）
    """
    fp = os.path.join(SAVES_DIR, f'存档{save_num}.json')
    if not os.path.exists(fp):
        return None
    try:
        with open(fp, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        # 自动恢复回声系统状态到 world_state.json
        restore_echo_setting(data)
        # 从存档中还原场景缓存
        try:
            from save.scene_cache import extract_scene_cache
            cache_file = extract_scene_cache(data)
            if cache_file:
                pass  # 缓存已还原到工作目录
        except ImportError:
            pass  # 场景缓存模块不存在时静默跳过
        return data
    except Exception:
        return None


def restore_echo_setting(save_data):
    """
    从存档恢复回声系统状态到 world_state.json
    
    参数:
        save_data: dict - 存档数据
    """
    try:
        import state.world as w
        ws = w.load_world_state()
        echo_val = save_data.get('echoEnabled')
        if echo_val is not None:
            ws['echo_enabled'] = bool(echo_val)
            w.save_world_state(ws)
    except Exception:
        pass


def write_save(save_data):
    """
    自动编号写入新存档（仅存档，不生成剧情摘要）
    
    参数:
        save_data: dict - 存档数据
    
    返回:
        str: 写入的文件名
    """
    save_num = _get_next_save_num()
    filename = f'存档{save_num}.json'
    fp = os.path.join(SAVES_DIR, filename)
    
    if 'timestamp' not in save_data:
        save_data['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 从 world_state.json 同步回声状态（如果 party 数据存在）
    try:
        import state.world as w
        ws = w.load_world_state()
        save_data['echoEnabled'] = ws.get('echo_enabled', False)
    except Exception:
        pass
    
    # 将当前场景缓存嵌入存档
    try:
        from save.scene_cache import embed_scene_cache
        embed_scene_cache(save_data)
    except ImportError:
        pass
    
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    
    return filename


def write_save_with_summary(save_data, recent_events=None):
    """
    写入存档并自动生成剧情摘要（规则14.7强制流程）
    
    参数:
        save_data: dict - 存档数据
        recent_events: list[str] - 最近的关键事件列表（可选）
    
    返回:
        tuple: (存档文件名, 摘要文本)
    """
    # 1. 写入存档
    archive_file = write_save(save_data)
    
    # 2. 生成剧情摘要
    try:
        # 从存档数据中提取世界状态和角色信息
        party = save_data.get('party', [])
        character_levels = {
            p.get('name', f'角色{i+1}'): p.get('level', 1)
            for i, p in enumerate(party)
        }
        
        # 尝试读取 world_state.json
        world_state = None
        npc_status = {}
        if os.path.exists('world_state.json'):
            with open('world_state.json', 'r', encoding='utf-8') as f:
                try:
                    world_state = json.load(f)
                    npc_status = world_state.get('key_npc_status', {})
                except:
                    world_state = {'current_chapter': save_data.get('chapter', 1),
                                   'current_scene': save_data.get('location', '未知'),
                                   'day_in_game': 1}
        else:
            world_state = {'current_chapter': save_data.get('chapter', 1),
                           'current_scene': save_data.get('location', '未知'),
                           'day_in_game': 1}
        
        # 从 save_data 中提取已完成节点作为事件
        completed = save_data.get('completedNodes', [])
        events = list(recent_events or [])[:3]
        if not events and completed:
            events = completed[-3:]
        if not events:
            events = [f'存档于 {save_data.get("location", "未知")}']
        
        # 调用剧情摘要生成
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from summary.generate import generate_plot_summary, save_summary
        summary_text = generate_plot_summary(
            world_state, npc_status, events, character_levels
        )
        save_summary(summary_text,
                     save_data.get('chapter', 1),
                     save_data.get('location', '未知'))
        return archive_file, summary_text
    except Exception as e:
        # 摘要生成失败不影响存档本身
        return archive_file, f'[摘要生成失败: {e}]'


def get_save_display(saves):
    """
    生成存档列表的展示文本
    
    参数:
        saves: list[dict] - list_saves() 的返回值
    
    返回:
        str: 展示文本
    """
    if not saves:
        return "📂 没有找到存档。"
    
    lines = [f"📂 共 {len(saves)} 个存档：", ""]
    for s in saves:
        lines.append(f"  {s['num']}. `{s['filename']}` — {s['timestamp']}")
        lines.append(f"     第{s['chapter']}章 · {s['location']} · {s['party_levels']}")
    return "\n".join(lines)
def rebuild_live_party(save_data):
    """
    从存档数据重建 live_party.json（完整格式）。
    提取角色的完整属性、技能、攻击、法术、装备等数据。
    """
    from datetime import datetime
    
    party = []
    save_party = save_data.get('party', [])
    team = save_data.get('全队状态', {})
    
    for i, sp in enumerate(save_party):
        if isinstance(sp, str):
            continue
        name = sp.get('name', '')
        items_raw = sp.get('items', [])
        
        char = {
            'name': name,
            'race': sp.get('race', ''),
            'class': sp.get('class', ''),
            'subclass': sp.get('sub', ''),
            'level': sp.get('level', 1),
            'hp': {'current': sp.get('hp', sp.get('maxHp', 10)),
                   'max': sp.get('maxHp', sp.get('hp', 10)),
                   'temp': sp.get('tempHp', 0)},
            'ac': sp.get('ac', 10),
            'acDetail': sp.get('acDetail', ''),
            'initiative': sp.get('initiative', 0),
            'speed': sp.get('speed', 30),
            'proficiency': sp.get('proficiencyBonus', 2),
            'xp': {'current': sp.get('xp', 0), 'toNext': sp.get('lvlUpXp', 300)},
            'stats': sp.get('stats', {}),
            'saves': sp.get('savingThrows', {}),
            'skills': [],
            'resources': [],
            'features': sp.get('classFeatures', []) + sp.get('raceTraits', []),
            'attacks': sp.get('attacks', []),
            'spells': sp.get('spells', []),
            'spellcasting': sp.get('spellcasting', {}),
            'spellSlots': sp.get('spellSlots', {}),
            'equipped': sp.get('equipped', []),
            'backpack': [],
            'armor': sp.get('armor', ''),
            'gear': sp.get('gear', ''),
            'inventory': sp.get('inventory', []),
        }
        
        # Convert skills from dict format to array
        skills_dict = sp.get('skills', {})
        char['skills'] = [
            {'name': k, 'prof': v, 'expertise': False, 'mod': 0}
            for k, v in skills_dict.items() if isinstance(v, bool)
        ]
        
        # Convert saves from dict to proper format
        saves_dict = sp.get('savingThrows', {})
        char['saves'] = {k: 2 if v else 0 for k, v in saves_dict.items()}
        
        # Separate inventory into backpack
        inv = sp.get('inventory', [])
        char['backpack'] = [i.get('name', '') for i in inv if isinstance(i, dict)]
        
        party.append(char)
    
    live_data = {
        '_说明': '实时角色状态（完整格式，由 sync_live_from_save 生成）',
        '_格式版本': '2.0',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'chapter': save_data.get('chapter', 1),
        'location': save_data.get('location', ''),
        'party': party,
    }
    
    # Write to live_party.json
    fp = 'live_party.json'
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(live_data, f, ensure_ascii=False, indent=2)
    
    return live_data
