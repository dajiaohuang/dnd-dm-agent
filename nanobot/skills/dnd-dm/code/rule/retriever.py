"""
规则动态检索：按关键词搜索 DM_RULES.md 中的具体规则
替代按层全量加载，只注入匹配的规则文本
"""
import os
import re

# 规则关键词映射表
# 格式：(关键词列表) → 规则号
RULE_KEYWORDS = [
    (['检定', '投骰', '技能', 'DC', '优势', '劣势'], '1'),
    (['动作', '检定匹配', '技能对应'], '1.5'),
    (['行动空间', '选项', '下一步'], '2'),
    (['地图', '场景', '新场景', 'Excel'], '2b'),
    (['问队友', '推进', 'NPC提示'], '3'),
    (['支线', '填充', '跑偏', '护栏'], '4'),
    (['经验', 'XP', '升级经验'], '5'),
    (['时间', '推算', '时间线', '日程'], '7'),
    (['章节', '过渡', '加载文件', '场景切换'], '8'),
    (['模板', '格式', '展示'], '9'),
    (['红线', '不能', '禁止', 'NO'], '10'),
    (['模组构建', '自动构建'], '12'),
    (['升级', '等级提升', '新能力'], '13'),
    (['存档', '保存', '读档', '快速保存'], '14'),
    (['战斗', '回合', '先攻', 'HP', '态势', '战斗表'], '15'),
    (['装备', '装备加成', 'AC', 'DC计算'], '15b'),
    (['角色状态', '实时状态', 'live_party'], '16'),
    (['信息管控', '提前泄漏', '骰子说话'], '17'),
    (['代码模板', 'code/', '函数调用'], '18'),
    (['新开一局', '模组选择', '建卡'], '0a'),
    (['启动', '对话开始', '会话启动'], '0'),
    (['回声', 'echo', '现实映射', '隐喻', '心情', '情感'], '20'),
]

# 规则标题映射
RULE_TITLES = {
    '0': '会话启动协议',
    '0a': '新开一局/模组选择',
    '0.5': '交互方式微调',
    '1': '检定交互流程',
    '1.5': '动作-检定匹配',
    '2': '检定后行动空间',
    '2b': '地图子系统',
    '3': '问队友推进',
    '4': '支线填充控制',
    '5': '经验结算',
    '7': '场景时间推算',
    '8': '模组加载与剧情控制',
    '9': 'DM信息展示模板',
    '10': '行为红线',
    '12': '模组内容自动构建',
    '13': '角色升级完整记录',
    '14': '存档完整记录',
    '15': '战斗回合自动展示',
    '15b': '装备加成战斗结算',
    '16': '实时角色状态文件',
    '17': '检定信息管控',
    '18': '运行时代码模板优先',
    '19': 'Token优化—上下文优先级',
    '20': '回声映射—现实隐喻→奇幻叙事',
}


def retrieve_by_keyword(keyword):
    """
    按关键词检索匹配的规则号

    参数:
        keyword: str - 搜索关键词

    返回:
        list[str]: 匹配的规则号列表，按相关度排序
    """
    keyword_lower = keyword.lower()
    matches = []

    for keywords, rule_id in RULE_KEYWORDS:
        for kw in keywords:
            if kw.lower() in keyword_lower:
                matches.append(rule_id)
                break

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for r in matches:
        if r not in seen:
            seen.add(r)
            unique.append(r)

    return unique


def retrieve_by_keywords(keywords):
    """
    多关键词检索

    参数:
        keywords: list[str] - 关键词列表

    返回:
        list[str]: 规则号
    """
    all_matches = set()
    for kw in keywords:
        matches = retrieve_by_keyword(kw)
        all_matches.update(matches)
    return sorted(all_matches, key=lambda x: float(x.replace('b', '.5').replace('a', '.2')) if x.replace('b', '.5').replace('a', '.2').replace('.', '').isdigit() else 999)


def get_rule_titles(rule_ids):
    """
    获取规则号对应的标题列表

    参数:
        rule_ids: list[str] - 规则号列表

    返回:
        list[tuple]: [(rule_id, title), ...]
    """
    return [(rid, RULE_TITLES.get(rid, '?')) for rid in rule_ids]


def extract_rule_text(dm_rules_path, rule_id):
    """
    从 DM_RULES.md 中提取指定规则的全文

    参数:
        dm_rules_path: str - DM_RULES.md 的路径
        rule_id: str - 规则号（如 '1', '15b', '0a'）

    返回:
        str: 规则全文，如未找到返回 None
    """
    if not os.path.exists(dm_rules_path):
        return None

    with open(dm_rules_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the rule section
    # Rules are formatted as "## 规则X：" or "## 规则X："
    # Need to handle rule_id like '15b', '1', etc.
    pattern = re.compile(rf'##\s*规则{re.escape(rule_id)}\s*：')
    match = pattern.search(content)
    if not match:
        return None

    start = match.start()

    # Find the next rule or end of file
    next_rule = re.search(r'##\s*规则\d', content[start + len(match.group()):])
    if next_rule:
        end = start + len(match.group()) + next_rule.start()
    else:
        end = len(content)

    return content[start:end].strip()


def load_rules_by_keywords(dm_rules_path, keyword):
    """
    一键操作：关键词 → 规则号 → 提取全文

    参数:
        dm_rules_path: str
        keyword: str

    返回:
        dict: {matched_rules: [{rule_id, title, text}], raw_keyword: str}
    """
    rule_ids = retrieve_by_keyword(keyword)
    results = []
    for rid in rule_ids:
        text = extract_rule_text(dm_rules_path, rid)
        if text:
            results.append({
                'rule_id': rid,
                'title': RULE_TITLES.get(rid, '?'),
                'text': text
            })

    return {
        'keyword': keyword,
        'matched_rules': results,
        'count': len(results)
    }
