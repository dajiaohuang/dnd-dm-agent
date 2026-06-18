# D&D 5e AI 地下城主引擎

> 基于 **2024版 D&D 5e 规则** 的智能 AI 地下城主系统。纯对话交互，不需要专用客户端，任何 IM 软件即可运行。

纯对话式 AI 地下城主——你只需要告诉 AI "我想跑团"，它就自动接管一切：规则裁决、剧情推进、骰子判定、战斗结算。适合单人跑团或小型队伍。

---

## 功能

| 功能 | 说明 |
|------|------|
| **规则裁决** | 严格按 2024 版 D&D 5e 结算，6 步裁决流程 |
| **战斗系统** | 先攻表、回合管理、HP/AC 追踪、状态标记、死亡判定 |
| **角色创建** | 7 阶段对话式创建，属性/种族/职业/装备/法术全流程 |
| **情绪回声映射** (Echo) | 🌊 将玩家现实情绪隐喻映射为奇幻支线任务 |
| **存档系统** | 自动递增存档，附带剧情摘要 |
| **世界状态** | 派系关系、任务进度、NPC 状态、地点发现 |
| **Token 优化** | P0-P5 层级加载，日常运行仅 8-10K tokens |

## 快速开始

### 前提

- **OpenClaw**（推荐）或任何兼容 AgentSkills 的 AI 平台
- 同时安装 `dnd5e-srd` skill 提供 SRD 参考文档
- 自行准备 [2024 版 D&D 三宝书](https://www.dndbeyond.com/) 文本文件放入 `rules/` 目录

### 安装 (OpenClaw)

1. 通过 OpenClaw 插件市场安装 `dnd-dm` 技能
2. 将 `references/` 目录中的引用文件复制到工作目录根
3. 启动后自动扫描可用模组，选择载入或开新局

### 手动部署

将本仓库克隆或下载到你的 AI 工作目录：

```bash
git clone https://github.com/ackiles/dnd-dm-skill.git
```

参考 `references/DM_DEV_GUIDE.md` 完成环境配置。

## 文件结构

```
dnd-dm-skill/
├── SKILL.md                    ← 技能主定义文件
├── CHANGELOG_DM_v2.md          ← 版本日志
├── _meta.json                  ← 技能元数据
├── code/                       ← 代码模板库
│   ├── combat/                 ⚔️ 战斗结算
│   ├── dice/                   🎲 骰子
│   ├── echo/                   🌊 回声映射
│   ├── module/                 📦 模组加载
│   ├── party/                  👥 角色管理
│   ├── save/                   💾 存档
│   ├── state/                  📋 世界状态
│   └── rule/                   🔍 规则检索
├── references/                 ← DM 运行参考文档
│   ├── DM_RULES.md             📐 19条运行规则
│   ├── DM_TEMPLATES.md         📋 信息展示模板
│   ├── DM_MAP_SYS.md           🗺️ 地图系统
│   ├── CHAR_CREATION.md        🧙 角色创建流程
│   ├── MODULE_ARC.md           🏰 模组框架
│   ├── ECHO_ARC.md             🌊 回声映射参考
│   └── ...
├── items/                      ← 物品/魔法物品记录
├── srd/                        ← D&D 5e SRD 参考
└── rules/                      ← (自行准备) 三宝书
```

## Echo 回声映射系统

**将现实生活中的情绪与事件隐喻映射为 D&D 奇幻支线任务。**

```bash
python code/echo/echo_generator.py map "今天工作好烦" "玩家名"
# → 输出: "限时拯救" 任务骨架

python code/echo/echo_generator.py quest "加班累死了" "玩家名" --llm
# → LLM 增强: 生成完整场景/NPC/战斗/奖励
```

详细说明见 `references/ECHO_ARC.md` 和 `code/echo/mapper_rules.md`。

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0.14 | 2026-06-08 | 代码优化 + 物品管理 |
| v1.0.13 | 2026-05-31 | 🌊 Echo 回声映射系统 + LLM 精细映射 |
| v1.0.12 | 2026-05-23 | 场景懒加载 + 世界状态 + 剧情摘要 |
| v1.0.11 | 2026-05-23 | SRD 5.2.1 集成 |
| v1.0.10 | 2026-05-22 | 规则分层重构 |
| v1.0.5 | 2026-05-20 | 初始版本 |

完整日志见 `CHANGELOG_DM_v2.md`。

## 相关项目

- [Godlike DM](https://github.com/ackiles/godlike-dm) — 与 Owlbear Rodeo VTT 集成的战斗可视化扩展

## 许可

本项目基于 **D&D 5e SRD 5.2.1**（CC-BY-4.0 许可），不包含 Wizards of the Coast 的完整规则书内容。2024 版三宝书原文需自行准备。
