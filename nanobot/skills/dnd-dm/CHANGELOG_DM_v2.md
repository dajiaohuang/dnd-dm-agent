# DM 城主系统更新日志

## 1.0.13 (2026-05-31)

### 新增
- **🌊 回声映射系统 (Echo)**：现实→奇幻自动叙事引擎
  - `code/echo/echo_generator.py` — 情绪提取 + 主题分类 + 任务骨架生成
  - `code/echo/mapper_rules.md` — 映射规则全集（情绪→氛围、现实→奇幻、人物→NPC）
  - `code/echo/themes.json` — 玩家情绪记录 + 已生成任务档案
- **DM_RULES.md 规则20：回声映射** — 映射流程定义 + 5条隐私红线 + P0-P5 优先级
- **CODE_LIBRARY.md 注册 echo 函数**：`extract_mood()` / `extract_themes()` / `map_to_quest()` / `save_to_journal()`

### 映射规则
| 现实主题 | 奇幻任务 | 隐喻示例 |
|---------|---------|---------|
| 工作压力 | 限时拯救 | 老板→暴君，Deadline→诅咒倒数 |
| 人际关系 | 外交斡旋 | 争吵→派系冲突 |
| 个人成长 | 试炼之路 | 瓶颈→封印锁链 |
| 健康 | 寻药之旅 | 生病→诅咒疫病 |
| 经济 | 偿还契约 | 没钱→灵魂债契 |
| 成就 | 加冕时刻 | 升职→血脉觉醒 |

### 隐私红线
- 玩家不主动倾诉，绝不追问私人生活
- 映射必须转换隐喻，不直接复制现实
- 映射解密权在玩家
- themes.json 本地存储，不推送 GitHub

## 1.0.12 (2026-05-23)

### 新增
- **场景级懒加载系统**：`code/module/scene_index.py` — 按 `##` 标题建立场景行范围索引，只加载当前场景原文（~2.5K tokens）替代整章文件（~27-55K tokens）
- **结构化世界状态**：`code/state/world.py` + `world_state.json` — 6种数据结构（派系关系/任务进度/NPC状态/地点发现/游戏天数/当前场景），内置增量更新 API
- **剧情摘要系统（P0）**：`code/summary/generate.py` + `plot_summary.json` — 每次存档自动生成100-300 token摘要，会话启动优先加载摘要替代完整聊天历史
- **规则关键词检索**：`code/rule/retriever.py` — 19组关键词→规则ID映射，按需加载规则文本替代整层加载
- **通用模块初始化**：`code/module/init.py` — `init_module(module_name)` 接受任意模组名，自动生成通用模板数据文件（零硬编码）
- **玩家端检定展示函数**：`code/combat/checks.py` — `resolve_skill_check()`、`check_hit_v2()`、`resolve_save_check()`，显示完整加值构成明细

### 改进
- **通用性架构重构**：所有代码/数据文件消除特定模组硬编码引用（博德之门：坠入阿弗纳斯），支持任意 D&D 5e 模组
- **MODULE_INDEX.md 压缩 77%**：从 12.4K 降至 2.8K，改为指针格式（引用场景索引+world_state 动态摘要）
- **MODULE_ARC.md 压缩 87%**：从 6.9K 降至 0.9K，通用运行结构模板
- **world_state.json 通用化**：从硬编码6派系+12任务+7NPC 改为通用空模板，运行时动态填充
- **scanner 支持中英文场景标签**：`/combat|战斗|battle|fight/` 等双语关键词
- **DM_RULES.md 审计清理**：开发流程规则（18.3-18.6、12.1、19.4）移至 DM_DEV_GUIDE.md，所有规则通用化

### 架构变更
- **5级上下文加载优先级（P0-P5）**：DM_RULES.md 规则19
  - P0 常驻（~2.3K）：SOUL + 世界状态摘要 + 剧情摘要
  - P1 当前场景（~3.5-6.5K）：场景原文 + NPC 数据
  - P2 对话（~2K）：最近5轮
  - P3+P4 按需检索（0-3K）
  - 总计 ~5.8K-12K（优化前 ~50K-75K，节省 70-85%）
- **存档即摘要**：`write_save_with_summary()` 将存档与剧情摘要生成绑定
- **会话启动流**：`load_summary()` → 摘要文本（100-300t） → 按需恢复完整历史

### 技术细节
- `scene_index.py` 使用 `module_name:filename` 键格式避免多章覆盖
- `world.py` 提供 `get_world_summary()` 返回50-100 token摘要字符串
- `plot_summary.json` 增量更新保留核心内容+追加最新事件，上限6行
- `retriever.py` 的 `load_rules_by_keywords()` 替代加载整层（5K→0.5-2K tokens）

## 1.0.11 (2026-05-23)

### 新增
- **SRD 5.2.1 集成**：`srd/references/` 部署 20 个 SRD 参考文件 + `srd/scripts/` Python 搜索脚本
- **Related Skills 声明**：`SKILL.md` 新增「相关技能」节，声明与 `dnd5e-srd` 的配合关系

### 变更
- 插件文件结构：新增 `srd/` 子目录
- 版本号 1.0.10 → 1.0.11

### 引用说明
- SRD 文件来源：`dnd5e-srd` skill（原始安装路径 `~/AppData/Roaming/LobsterAI/SKILLs/dnd5e-srd/references/`）
- 20 个参考文件 DND5eSRD_*.md，共 1.47 MB
- SRD 5.2.1 基于 CC-BY-4.0 许可

## 1.0.10 (2026-05-22)

### 重大变更
- **DM_RULES.md 分层重构**：扁平23节 → 6层结构（层0常驻~层5模组控制），按场景按需细读
- **新增 DM_RULES_INDEX.md**：层级索引文件，每次会话常驻，指导 LLM 按场景查阅对应层
- **规则13压缩**（升级）：10项清单+触发时机保留，详细逐项引导整合回 body
- **规则14压缩**（存档）：触发时机+格式验证保留，完整字段定义整合回 body
- **规则18压缩**（代码模板）：13行对照表+沙盒/兜底/入库/同步全部保留
- **规则0+规则6合并**：重复的会话启动协议合并为一条
- **SOUL.md 更新**：增加 DM_RULES_INDEX.md 引用
- **DM_DEV_GUIDE.md 新增开发规则6**：新规则必须归层，跨层规则拆分的强制流程
- **DM_RULES.md 新增新规则归层约束**：`<!-- layer: N -->` 标记 + INDEX 同步，违反视为结构污染
- **Token 节省**：日常探索~2,500 tokens（原~4,925），节省~49%

## 1.0.8 (2026-05-22)

### 新增
- **规则18：运行时代码模板优先** — 运行时强制使用 `code/` 预构建函数代替 LLM 生成代码，大幅减少 Token 消耗
- **规则18.6：玩家新交互需求自动入库** — 玩家提出的新交互首次生成的代码自动登记到 `CODE_LIBRARY.md`
- **代码模板库** `code/`：战斗状态持久化（`combat/state.py`）、战斗公式模板（`combat/formulas.py`）、模组内容缓存（`module/cache.py`）、存档格式模板（`save/templates.py`）、规则速查表（`quickref.py`）
- `DM_DEV_GUIDE.md` 开发规则4：运行时代码模板使用流程 + Token 优化策略表
- **文件整理**：DM_RULES.md 内容迁移至 DM_TEMPLATES.md（展示模板）、DM_DEV_GUIDE.md（开发规则）

### 变更
- `DM_RULES.md` 精简至 825 行（原 937 行），剥离模板和开发内容
- `DM_TEMPLATES.md` 扩展至 439 行，新增 9.8-9.11 模板
- 法术展示模板全面更新：增加检定方式列和推荐理由列（规则11）
- 角色卡模板更新：已装备物品单独列出并标注生效属性（9.6a/9.6b）
- 存档规则：自动递增编号 + 时间戳记录（规则14）
- 战斗结算规则：装备加成全面纳入计算（规则15b）
- 升级流程：7步逐项引导（规则13.2）
- 模组选择：新开一局先选模组（规则0a）
- 检定信息管控：先描述情景再叫投骰（规则17）

## 1.0.6 (2026-05-21)

### 变更
- 文件重组：展示模板移入 `DM_TEMPLATES.md`，开发规则移入 `DM_DEV_GUIDE.md`
- 技能包发布结构更新

## 1.0.5 (2026-05-20)

### 新增
- 初始版本：D&D 5e 2024版 DM 城主系统
- 14条运行规则（规则0-16）
- DM 展示模板（9.1-9.6）
- 地图系统（DM_MAP_SYS.md）
- 角色创建流程（CHAR_CREATION.md）
- 存档/升级系统
