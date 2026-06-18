"""
模组文件构建器：生成 MODULE_STRUCTURE / MODULE_NODES 等文件
"""
import os, sys
import re
import json
sys.path.insert(0, os.path.dirname(__file__) or '.')
from scanner import scan_modules, get_module_level_range
# Lazy import for scene_index to avoid circular dependency
_scene_index_available = None


def build_module_structure_files(module_name, output_dir='.'):
    """
    构建模组的结构文件：MODULE_STRUCTURE.md, MODULE_NODES.md, MODULE_FLOW.md
    
    参数:
        module_name: str - 模组名
        output_dir: str - 输出目录（默认当前目录）
    
    返回:
        list[str]: 已创建的文件路径列表
    """
    modules = scan_modules()
    mod = None
    for m in modules:
        if m['name'] == module_name:
            mod = m
            break
    
    if not mod:
        return []
    
    level_range = get_module_level_range(mod['files'])
    created = []
    
    # 1. MODULE_STRUCTURE.md
    struct_lines = [
        f"# {module_name} — 章节结构",
        "",
        "## 章节加载序列",
        "",
        f"| 顺序 | 文件 | 等级范围 | 里程碑 |",
        f"|:---:|------|:--------:|--------|",
    ]
    for i, ch in enumerate(mod['chapters'], 1):
        struct_lines.append(f"| {i} | `{ch}` | {level_range['min'] if level_range else '?'}-{level_range['max'] if level_range else '?'} | - |")
    
    struct_path = os.path.join(output_dir, 'MODULE_STRUCTURE.md')
    with open(struct_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(struct_lines))
    created.append(struct_path)
    
    # 2. MODULE_NODES.md (placeholder)
    nodes_lines = [
        f"# {module_name} — 关键节点清单",
        "",
        "| 编号 | 节点名 | 章节 | 触发条件 | 状态 |",
        "|:---:|--------|:----:|----------|:----:|",
        "| 1 | [节点名] | Ch.1 | [触发条件] | 🔒未解锁 |",
    ]
    nodes_path = os.path.join(output_dir, 'MODULE_NODES.md')
    with open(nodes_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(nodes_lines))
    created.append(nodes_path)
    
    # 3. MODULE_FLOW.md (placeholder)
    flow_lines = [
        f"# {module_name} — 事件流程图",
        "",
        "```",
        "[起始] → [事件1] → [事件2] → ... → [章节结束]",
        "  ├→ [支线A] → [完成/失败]",
        "  └→ [支线B] → [完成/失败]",
        "```",
    ]
    flow_path = os.path.join(output_dir, 'MODULE_FLOW.md')
    with open(flow_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(flow_lines))
    created.append(flow_path)
    
    # 4. MODULE_INDEX.md (placeholder)
    index_lines = [
        f"# {module_name} — 索引",
        "",
        "## NPC 名册",
        "",
        "| NPC | 角色 | 位置 | 状态 |",
        "|-----|------|------|:----:|",
        "| [NPC名] | [角色] | [位置] | ❓ |",
    ]
    index_path = os.path.join(output_dir, 'MODULE_INDEX.md')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(index_lines))
    created.append(index_path)
    
    return created


def scan_chapter_structure(filepath):
    """
    【章节过渡用】扫描单个章节文件的结构，返回结构化信息
    
    参数:
        filepath: str - 章节文件路径
    
    返回:
        dict:
            scenes: list[dict] - 场景列表 {level, title, items}
            encounter_count: int - 遭遇数量
            key_npcs: list[str] - 提及的关键 NPC
            has_运作章节: bool - 是否包含运作本章的导言
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    
    # 逐行扫描结构
    scenes = []
    current_h2 = None
    current_h3 = None
    encounter_count = 0
    key_npcs = set()
    
    # 基础NPC关键词
    npc_pattern = re.compile(r'[A-Z\u4e00-\u9fff]{2,}(?:[·•]?[A-Z\u4e00-\u9fff]+)*')
    
    for line in lines:
        s = line.strip()
        
        if s.startswith('## '):
            # Level 2 heading - new major section
            current_h2 = s.lstrip('# ')
            current_h3 = None
            scenes.append({
                'level': '##',
                'title': current_h2,
                'type': 'section',
                'items': []
            })
        elif s.startswith('### ') and current_h2:
            current_h3 = s.lstrip('# ')
            scenes.append({
                'level': '###',
                'title': current_h3,
                'type': 'subsection',
                'parent': current_h2,
                'items': []
            })
        elif s.startswith('#### '):
            item_title = s.lstrip('# ')
            scenes.append({
                'level': '####',
                'title': item_title,
                'type': 'subitem',
                'parent': current_h3 or current_h2
            })
            # Count likely encounters
            if any(kw in item_title for kw in ['遭遇', '攻击', '战斗', '冲突', '巡逻', '推销', '陷阱']):
                encounter_count += 1
    
    has_运作章节 = '运作本章' in content or '运作' in content[:2000]
    
    return {
        'scenes': scenes,
        'encounter_count': encounter_count,
        'has_intro': has_运作章节,
        'total_sections': len([s for s in scenes if s['level'] == '##']),
        'total_subsections': len([s for s in scenes if s['level'] == '###']),
        'total_subitems': len([s for s in scenes if s['level'] == '####'])
    }


def build_chapter_content(chapter_filepath, chapter_label, module_name=None):
    """
    【章节过渡用】扫描章节文件并更新 MODULE_INDEX.md 和 MODULE_ARC.md
    不依赖特定模组名称——模块信息通过 scanner 自动发现
    
    参数:
        chapter_filepath: str - 章节文件的完整路径
        chapter_label: str - 章节标签（如 "Ch.2"）
        module_name: str - 可选，模块名（不传时从 scanner 自动匹配）
    
    返回:
        dict: 章节结构信息，含 scene_index 构建结果
    """
    if not os.path.exists(chapter_filepath):
        return {'error': f'File not found: {chapter_filepath}'}
    
    # 1. 扫描章节结构
    structure = scan_chapter_structure(chapter_filepath)
    structure['chapter_file'] = os.path.basename(chapter_filepath)
    structure['chapter_label'] = chapter_label
    
    # 2. 自动识别模块名（若未提供）
    if not module_name:
        from scanner import scan_modules, _identify_module
        modules = scan_modules()
        basename = os.path.basename(chapter_filepath)
        module_name = _identify_module(basename)
        # Also try to match by path
        for mod in modules:
            if chapter_filepath in mod.get('files', []):
                module_name = mod['name']
                break
    
    # 3. 提取等级范围（从文件内容）
    with open(chapter_filepath, 'r', encoding='utf-8') as f:
        first_500 = f.read(500)
    level_match = re.search(r'(\d+)[–\-~]\s*(\d+)\s*[级Lv]', first_500)
    if level_match:
        structure['level_range'] = f'{level_match.group(1)}-{level_match.group(2)}'
    else:
        # Try to get from scanner
        from scanner import get_module_level_range
        lr = get_module_level_range([chapter_filepath])
        if lr:
            structure['level_range'] = f'{lr["min"]}-{lr["max"]}'
        else:
            structure['level_range'] = None
    
    # 4. 提取主要场景列表和子区域
    main_scenes = [s['title'] for s in structure['scenes'] 
                   if s['level'] == '##' and s['title'] != '运作本章'
                   and s['title'] != 'Running the Chapter']
    structure['main_scenes'] = main_scenes
    
    sub_areas = {}
    for s in structure['scenes']:
        if s['level'] == '###' and s.get('parent') in main_scenes:
            parent = s['parent']
            if parent not in sub_areas:
                sub_areas[parent] = []
            sub_areas[parent].append(s['title'])
    structure['sub_areas'] = sub_areas
    
    # 5. 建立场景级行号索引
    try:
        global _scene_index_available
        if _scene_index_available is None:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
            from module.scene_index import build_scene_index, save_scene_index, set_current_scene
            _scene_index_available = True
        
        if _scene_index_available:
            scene_index = build_scene_index(chapter_filepath)
            if module_name:
                save_scene_index(module_name, scene_index)
                # Set first non-intro scene as current
                filename = os.path.basename(chapter_filepath)
                for scene in scene_index['scenes']:
                    if 'intro' not in scene.get('tags', []):
                        set_current_scene(
                            f'{module_name}:{filename}',
                            scene['title']
                        )
                        structure['current_scene'] = scene['title']
                        break
    except ImportError:
        _scene_index_available = False
    except Exception as e:
        print(f"[WARN] scene_index build failed: {e}")
    
    return structure
