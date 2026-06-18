"""
echo_generator.py — 回声映射引擎 v2
从玩家现实倾诉 → 生成 D&D 支线任务骨架

双阶段架构：
  阶段1（关键词）: 提取情绪+主题，确定任务模板
  阶段2（LLM 增强）: 调用配置的 LLM API 生成完整模组内容

用法:
  python echo_generator.py extract "文本"                          # 仅关键词
  python echo_generator.py map "文本" "玩家名"                      # 关键词映射 + 可选 LLM
  python echo_generator.py quest "文本" "玩家名"                    # 关键词任务 + 保存
  python echo_generator.py quest "文本" "玩家名" --llm              # LLM 增强任务 + 保存
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

ECHO_DIR = Path(__file__).parent
THEMES_PATH = ECHO_DIR / "themes.json"
CONFIG_PATH = ECHO_DIR / "echo_config.json"

# ── 配置加载 ──

def load_config() -> dict:
    """加载 LLM 和战役配置"""
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"llm": {"enabled": False}, "campaign": {}}


def llm_available() -> bool:
    """检查 LLM 是否已配置可用"""
    cfg = load_config()
    llm = cfg.get("llm", {})
    return llm.get("enabled") and bool(llm.get("api_key"))


# ── 情绪关键词映射表 ──

MOOD_KEYWORDS = {
    "焦虑":   ["焦虑", "紧张", "担心", "害怕", "不安", "慌", "急", "压力", "deadline", "截止"],
    "愤怒":   ["愤怒", "生气", "烦", "烦躁", "火大", "忍", "受不了", "气死", "吵架"],
    "悲伤":   ["难过", "伤心", "失落", "孤独", "孤单", "空虚", "想哭", "抑郁"],
    "疲惫":   ["累", "疲惫", "倦", "没劲", "不想动", "虚", "乏力"],
    "喜悦":   ["开心", "高兴", "爽", "快乐", "兴奋", "成就感", "满足", "不错"],
    "困惑":   ["困惑", "迷茫", "不懂", "不确定", "犹豫", "纠结", "选哪个"],
    "希望":   ["希望", "期待", "决心", "加油", "坚持", "进步", "突破"],
    "压抑":   ["压抑", "憋", "闷", "沉重", "无力", "被逼", "无可奈何"],
}

THEME_CATEGORIES = {
    "工作压力":   ["工作", "老板", "上司", "同事", "项目", "加班", "开会", "任务", "绩效", "升职"],
    "人际关系":   ["朋友", "家人", "对象", "分手", "吵架", "误会", "合不来", "社交"],
    "个人成长":   ["学习", "进步", "瓶颈", "迷茫", "选择", "方向", "目标", "梦想"],
    "健康":       ["病", "身体", "累", "失眠", "头疼", "体检", "医院"],
    "经济":       ["钱", "工资", "穷", "贵", "买不起", "负债", "省钱"],
    "生活日常":   ["搬家", "装修", "交通", "做饭", "家务", "琐事"],
    "成就":       ["完成", "成功", "通过", "得到", "收获", "晋升"],
}

# ── 映射表（简化版，完整版见 mapper_rules.md）──

MISSION_TEMPLATES = {
    "工作压力": {
        "type": "限时拯救",
        "hook": "一股古老诅咒正向城市蔓延，源头是一台被遗忘的计时器……",
        "structure": ["发现诅咒源", "追踪遗骸线索", "解除诅咒仪式", "面对压迫者化身"],
        "reward_hint": "解除诅咒后获得[解放之印]",
    },
    "人际关系": {
        "type": "外交斡旋",
        "hook": "两个古老家族之间的裂痕正在撕裂整个社区……",
        "structure": ["调查冲突起因", "收集双方证据", "斡旋谈判", "揭穿幕后挑拨者"],
        "reward_hint": "获得[和睦之徽]",
    },
    "个人成长": {
        "type": "试炼之路",
        "hook": "一座古老试炼塔在迷雾中显现，据说通过者将获得真知……",
        "structure": ["接受试炼邀请", "通过三重考验", "面对内心幻象", "获得力量馈赠"],
        "reward_hint": "获得[睿智指环]",
    },
    "健康": {
        "type": "寻药之旅",
        "hook": "一场神秘的枯萎症正在蔓延，唯一的解药只在幽暗地域深处……",
        "structure": ["寻找病源", "穿越危险区域", "获取解药", "净化仪式"],
        "reward_hint": "获得[生命之露]",
    },
    "经济": {
        "type": "偿还契约",
        "hook": "一份古老的债务契约被唤醒，欠债者的灵魂正在被拖入炼狱……",
        "structure": ["发现契约", "寻找债主真身", "完成补偿任务", "撕毁或履行契约"],
        "reward_hint": "获得[自由印玺]",
    },
    "生活日常": {
        "type": "日常侵扰",
        "hook": "一群小魔鬼在城市下水道筑巢，虽然不致命但让人不得安宁……",
        "structure": ["追踪侵扰源", "清理巢穴", "防止复发"],
        "reward_hint": "获得[安宁符文]",
    },
    "成就": {
        "type": "加冕时刻",
        "hook": "一位英雄的功绩终于被上层位面注意到……",
        "structure": ["收到邀请", "通过认可仪式", "接受祝福", "故事被传颂"],
        "reward_hint": "获得[英雄徽记]",
    },
}

# ── 阶段1：关键词情绪/主题提取 ──

def extract_mood(text: str) -> list:
    """提取文本中的情绪标签（关键词匹配）"""
    detected = []
    for mood, keywords in MOOD_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                detected.append(mood)
                break
    return detected if detected else ["中性"]


def extract_themes(text: str) -> list:
    """提取文本中的主题类别（关键词匹配）"""
    detected = []
    for theme, keywords in THEME_CATEGORIES.items():
        for kw in keywords:
            if kw in text:
                detected.append(theme)
                break
    return detected if detected else ["日常"]


# ── 阶段1：关键词任务骨架生成 ──

def map_to_quest(player_input: str, player_name: str = "冒险者") -> dict:
    """将玩家输入映射为 D&D 支线任务骨架（关键词）"""
    moods = extract_mood(text=player_input)
    themes = extract_themes(text=player_input)

    primary_theme = themes[0] if themes else "生活日常"
    template = MISSION_TEMPLATES.get(primary_theme, MISSION_TEMPLATES["生活日常"])
    mood_desc = "、".join(moods)

    quest = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "for_player": player_name,
            "from_input": player_input[:200],
            "detected_mood": mood_desc,
            "detected_theme": primary_theme,
            "refined_by_llm": False,
        },
        "quest": {
            "title": f"[回声] {template['type']}",
            "type": template["type"],
            "hook": template["hook"],
            "structure": template["structure"],
            "atmosphere": mood_desc,
            "reward_hint": template["reward_hint"],
        },
        "mapping": {
            "real_world_theme": primary_theme,
            "emotional_transfer": f"玩家的{mood_desc}情绪映射为任务的{template['type']}氛围",
        },
    }
    return quest


# ── 阶段2：LLM 精细映射 ──

def call_llm_api(prompt: str) -> str:
    """调用配置的 LLM API（OpenAI 兼容格式）"""
    cfg = load_config()
    llm = cfg.get("llm", {})
    api_url = llm.get("api_url", "")
    api_key = llm.get("api_key", "")
    model = llm.get("model", "deepseek-chat")
    max_tokens = llm.get("max_tokens", 1024)
    temperature = llm.get("temperature", 0.8)

    if not api_url or not api_key:
        raise RuntimeError("LLM 未配置：请先在 echo_config.json 设置 api_url 和 api_key")

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API HTTP {e.code}: {body[:200]}")
    except (KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"LLM API 响应解析失败: {e}")


def _parse_llm_output(text: str) -> dict:
    """尝试从 LLM 回复中提取 JSON block，失败则返回原始文本"""
    # 优先找 ```json ... ``` 代码块
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start) if "```" in text[start:] else len(text)
        text = text[start:end].strip()
    # 再尝试找 ``` ... ```
    elif text.startswith("```"):
        lines = text.split("\n")
        if len(lines) > 2:
            text = "\n".join(lines[1:-1])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw_llm_output": text}


_BUILD_PROMPT = """你是一个 D&D 5e 地下城主的回声映射助手。你的任务是将玩家现实生活中的情绪事件，隐喻映射为 D&D 奇幻模组任务。

## 第一阶段输入（关键词提取结果）

玩家表达：
{player_input}

玩家角色名：{player_name}

关键词抽取结果：
- 情绪：{mood}
- 主题：{theme}
- 任务模板：{template_type}
- 模板钩子：{template_hook}

战役背景：{setting}
队伍等级：{party_level} 级（{party_size} 人）

## 你的任务（第二阶段：LLM 精细映射）

请以 JSON 格式输出以下内容，不要包含 markdown 代码块标记以外的任何解释文字：

```json
{{
  "refined_mood": "精炼后的情绪判断",
  "refined_theme": "精炼后的主题判断（如果不准确可修正关键词的误判）",
  "refined_title": "更有故事感的任务标题（20字以内）",
  "refined_hook": "更有沉浸感的任务钩子，2-3句话，含场景氛围",

  "refined_structure": [
    "步骤1 - 一句话描述",
    "步骤2 - 一句话描述",
    "步骤3 - 一句话描述",
    "...（3-5步）"
  ],

  "scene_descriptions": {{
    "intro": "开场场景描述，50字以内，含氛围和五感细节",
    "climax": "高潮场景描述，50字以内",
    "resolution": "结局场景描述，50字以内"
  }},

  "npcs": [
    {{"name": "NPC名字", "role": "角色定位", "personality": "一句话个性描述"}}
  ],

  "combat_encounter": {{
    "enemies": ["怪物1 数量", "怪物2 数量"],
    "terrain_features": ["地形特征1", "地形特征2"],
    "cr": "挑战等级估算"
  }},

  "reward": {{
    "item_name": "魔法物品名称",
    "item_effect": "物品效果（一句话）",
    "xp_bonus": "经验奖励说明"
  }},

  "metaphor_insight": "一句话解释这个任务映射了玩家的什么现实情绪（可选，仅供 DM 参考）"
}}
```

## 映射原则
1. 隐喻转换：老板→暴君/巫妖，Deadline→倒数计时，争吵→派系冲突
2. 情感保真：保留原始情绪强度
3. 战役融合：使任务自然融入{setting}的世界
4. 适合等级：战斗遭遇适合{party_level}级{party_size}人队伍
5. 包含社交、探索、战斗三种元素
"""


def llm_refine_quest(quest_draft: dict, player_input: str) -> dict:
    """阶段2：调用 LLM 精炼任务内容"""
    cfg = load_config()
    campaign = cfg.get("campaign", {})

    prompt = _BUILD_PROMPT.format(
        player_input=player_input[:500],
        player_name=quest_draft["meta"]["for_player"],
        mood=quest_draft["meta"]["detected_mood"],
        theme=quest_draft["meta"]["detected_theme"],
        template_type=quest_draft["quest"]["type"],
        template_hook=quest_draft["quest"]["hook"],
        setting=campaign.get("setting", "剑湾"),
        party_level=campaign.get("party_level", 5),
        party_size=campaign.get("party_size", 4),
    )

    raw_output = call_llm_api(prompt)
    refined = _parse_llm_output(raw_output)

    # 保留关键词骨架，用 LLM 输出覆盖增强字段
    quest_draft["meta"]["refined_by_llm"] = True
    quest_draft["quest"]["refined_title"] = refined.get("refined_title", quest_draft["quest"]["title"])
    quest_draft["quest"]["refined_hook"] = refined.get("refined_hook", quest_draft["quest"]["hook"])
    quest_draft["quest"]["refined_structure"] = refined.get("refined_structure", quest_draft["quest"]["structure"])
    quest_draft["quest"]["scene_descriptions"] = refined.get("scene_descriptions", {})
    quest_draft["quest"]["npcs"] = refined.get("npcs", [])
    quest_draft["quest"]["combat_encounter"] = refined.get("combat_encounter", {})
    quest_draft["quest"]["reward"] = refined.get("reward", {})
    quest_draft["mapping"]["refined_mood"] = refined.get("refined_mood", "")
    quest_draft["mapping"]["metaphor_insight"] = refined.get("metaphor_insight", "")
    quest_draft["_raw_llm_prompt"] = prompt
    quest_draft["_raw_llm_output"] = raw_output

    return quest_draft


# ── 存档 ──

def save_to_journal(quest: dict):
    """保存生成的任务到 journal"""
    themes = json.loads(THEMES_PATH.read_text(encoding="utf-8"))
    themes["generated_quests"].append(quest)
    themes["journal"].append({
        "timestamp": datetime.now().isoformat(),
        "mood": quest["meta"]["detected_mood"],
        "theme": quest["meta"]["detected_theme"],
        "title": quest["quest"].get("refined_title", quest["quest"]["title"]),
        "refined_by_llm": quest["meta"].get("refined_by_llm", False),
    })
    THEMES_PATH.write_text(json.dumps(themes, ensure_ascii=False, indent=2), encoding="utf-8")


def print_quest(quest: dict, show_raw: bool = False):
    """友好输出任务内容"""
    q = quest["quest"]
    m = quest["meta"]
    title = q.get("refined_title", q["title"])
    hook = q.get("refined_hook", q["hook"])
    structure = q.get("refined_structure", q["structure"])

    lines = [
        f"== '{title}' ==",
        f"    {m['for_player']} | {m['detected_mood']} | {m['detected_theme']}",
        f"",
        f"{hook}",
        f"",
        f"[任务步骤]",
    ]
    for i, step in enumerate(structure, 1):
        lines.append(f"  {i}. {step}")

    if q.get("scene_descriptions"):
        sc = q["scene_descriptions"]
        lines.append(f"")
        lines.append(f"[场景片段]")
        for key, desc in sc.items():
            label = {"intro": "开场", "climax": "高潮", "resolution": "结局"}
            lines.append(f"  [{label.get(key, key)}] {desc}")

    if q.get("npcs"):
        lines.append(f"")
        lines.append(f"[NPC]")
        for npc in q["npcs"]:
            lines.append(f"  - {npc.get('name', '?')} -- {npc.get('role', '')}")

    if q.get("combat_encounter"):
        ce = q["combat_encounter"]
        lines.append(f"")
        lines.append(f"[战斗遭遇] (CR {ce.get('cr', '?')})")
        for e in ce.get("enemies", []):
            lines.append(f"  - {e}")
        for t in ce.get("terrain_features", []):
            lines.append(f"  [地形] {t}")

    if q.get("reward"):
        r = q["reward"]
        lines.append(f"")
        lines.append(f"[奖励] {r.get('item_name', '')} -- {r.get('item_effect', '')}")

    if q.get("scene_descriptions") or q.get("npcs") or q.get("combat_encounter"):
        lines.append(f"")
        lines.append(f"[DM参考] {quest['mapping'].get('metaphor_insight', '')}")

    if m.get("refined_by_llm"):
        lines.append(f"")
        lines.append(f"[已通过 LLM 精细映射]")

    if show_raw and quest.get("_raw_llm_output"):
        lines.append(f"")
        lines.append(f"--- 原始 LLM 输出 ---")
        lines.append(quest["_raw_llm_output"])

    print("\n".join(lines))


# ── CLI ──

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "help"

    # ── help 命令 ──
    if command == "help":
        print("用法:")
        print("  python echo_generator.py extract <文本>")
        print("  python echo_generator.py map <文本> [玩家名] [--llm]")
        print("  python echo_generator.py quest <文本> [玩家名] [--llm]")
        print("  python echo_generator.py config                 # 查看当前配置")
        sys.exit(0)

    # ── config 命令 ──
    if command == "config":
        cfg = load_config()
        llm = cfg.get("llm", {})
        status = "已启用" if llm.get("enabled") else "未启用（仅关键词匹配）"
        print(f"LLM 状态: {status}")
        print(f"API URL:  {llm.get('api_url', '未设置')}")
        print(f"模型:     {llm.get('model', '未设置')}")
        key = llm.get("api_key", "")
        masked = "已设置 (" + key[:6] + "...)" if key else "未设置"
        print(f"API Key:  {masked}")
        camp = cfg.get("campaign", {})
        print(f"战役:     {camp.get('setting', '未设置')} | Lv{camp.get('party_level', '?')} x{camp.get('party_size', '?')}")
        sys.exit(0)

    if len(sys.argv) < 3:
        print("用法: python echo_generator.py <命令> <文本> [玩家名] [--llm]")
        sys.exit(1)

    text = sys.argv[2]
    use_llm = "--llm" in sys.argv
    player = "冒险者"
    for arg in sys.argv[3:]:
        if arg != "--llm":
            player = arg
            break

    if command == "extract":
        moods = extract_mood(text)
        themes = extract_themes(text)
        print(f"情绪: {moods}")
        print(f"主题: {themes}")

    elif command in ("map", "quest"):
        quest = map_to_quest(text, player)
        if use_llm:
            if not llm_available():
                err = "LLM 未配置。请编辑 echo_config.json 填入 api_key 并设置 enabled=true"
                print(err)
                sys.exit(1)
            print("[正在调用 LLM 精细映射...]")
            try:
                quest = llm_refine_quest(quest, text)
            except RuntimeError as e:
                print(f"[错误] {e}")
                sys.exit(1)
        if command == "quest":
            save_to_journal(quest)
            print("(已保存到 themes.json)")
        print_quest(quest, show_raw=(use_llm and "--raw" in sys.argv))
