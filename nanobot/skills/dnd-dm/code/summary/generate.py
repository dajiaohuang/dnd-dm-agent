"""
剧情摘要生成：在关键事件/存档后自动生成100-300 token的剧情摘要
用于替代完整聊天历史，大幅减少上下文消耗
"""
import os
import json
import datetime

SUMMARY_FILE = 'plot_summary.json'


def get_default_summary():
    """返回默认剧情摘要结构"""
    return {
        "timestamp": "",
        "chapter": 0,
        "scene": "",
        "summary": "",
        "version": 1,
        "key_events": []
    }


def generate_plot_summary(world_state, npc_status, recent_events, character_levels):
    """
    生成剧情摘要（100-300 token）

    参数:
        world_state: dict - 世界状态
        npc_status: dict - NPC状态
        recent_events: list[str] - 最近的关键事件列表（1-3条）
        character_levels: dict - 角色等级 {name: level}

    返回:
        str: 剧情摘要文本
    """
    lines = []

    # 队伍状态
    if character_levels:
        level_strs = [f"{name} Lv.{lv}" for name, lv in character_levels.items()]
        lines.append(f"队伍: {' '.join(level_strs)}")

    # 当前位置
    chapter = world_state.get('current_chapter', '?')
    scene = world_state.get('current_scene', '?')
    day = world_state.get('day_in_game', 1)
    lines.append(f"位置: 第{chapter}章·{scene} (游戏第{day}天)")

    # 关键NPC关系
    important_npcs = {k: v for k, v in npc_status.items()
                      if any(kw in v for kw in ['存活', '友好', '敌对', '已'])}
    if important_npcs:
        npc_strs = [f"{n}({s.split('·')[0]})" for n, s in list(important_npcs.items())[:4]]
        lines.append(f"关键NPC: {' '.join(npc_strs)}")

    # 最近事件
    if recent_events:
        lines.append(f"最近: {' → '.join(recent_events[:3])}")

    # 进度状态
    quests = world_state.get('quest_progress', {})
    active = quests.get('进行中', [])
    completed = quests.get('完成', [])
    if active:
        lines.append(f"任务中: {' → '.join(active)}")
    if completed:
        lines.append(f"已完成: {', '.join(completed[-3:])}")  # Only last 3

    return '\n'.join(lines)


def save_summary(summary_text, chapter, scene, filepath=None):
    """
    保存剧情摘要

    参数:
        summary_text: str - 摘要文本
        chapter: int
        scene: str
        filepath: str - 可选
    """
    path = filepath or SUMMARY_FILE
    data = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "chapter": chapter,
        "scene": scene,
        "summary": summary_text,
        "version": 2
    }

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def load_summary(filepath=None):
    """
    载入剧情摘要

    参数:
        filepath: str - 可选

    返回:
        dict: 摘要数据或 None
    """
    path = filepath or SUMMARY_FILE
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return None


def update_summary(existing_summary, new_events, new_world_state, new_npc_status, character_levels):
    """
    增量更新摘要：保留核心内容 + 追加最新事件

    参数:
        existing_summary: str - 现有摘要文本
        new_events: list[str] - 新发生的事件
        new_world_state: dict
        new_npc_status: dict
        character_levels: dict

    返回:
        str: 更新后的摘要
    """
    core = existing_summary.split('\n')
    # Keep first 2 lines (队伍 + 位置)
    kept = core[:2] if len(core) >= 2 else core

    # Append new events
    if new_events:
        kept.append(f"最近: {' → '.join(new_events[:3])}")

    # NPCs
    important_npcs = {k: v for k, v in new_npc_status.items()
                      if any(kw in v for kw in ['存活', '友好', '敌对'])}
    if important_npcs:
        npc_strs = [f"{n}({s.split('·')[0]})" for n, s in list(important_npcs.items())[:4]]
        kept.append(f"关键NPC: {' '.join(npc_strs)}")

    # Max 6 lines
    return '\n'.join(kept[:6])
