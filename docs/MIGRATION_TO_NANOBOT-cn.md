# DM_agent 迁移到 nanobot 指南

> 适用仓库：`D:\mcp\DM_agent`  
> nanobot 源码：`D:\mcp\nanobot`  
> 文档基线：2026-06-18，nanobot `0.2.1`

## 1. 迁移目标

本次迁移的目标不是把 DND 业务代码复制进 nanobot，而是让 nanobot 接管通用 Agent 运行时，同时保留 DM_agent 已验证的 DND 领域内核。

迁移后的职责边界：

```text
QQ / WebUI / CLI
        |
        v
nanobot gateway
  - Provider 与模型调用
  - Agent loop / tool calling
  - 会话、上下文压缩、Skills、Subagent
  - NapCat 收发与通用 WebUI
        |
        v
DND adapter（nanobot Tool 插件）
  - 根据渠道身份解析战役、角色和 DM 权限
  - 选择当前模式与可用工具
  - 把工具结果和附件交还 nanobot
        |
        v
DM domain core
  - 战役、人物卡、设定、战斗、检定、骰子
  - SQLite/PostgreSQL 持久化
  - DiceAuditLog / CharacterChange / ToolCallAudit
  - XLSX 导入导出、规则库与资料库
```

核心原则：

1. **数据库是事实源**。nanobot 的 session/memory 只能保存对话上下文，不能代替战役状态、HP、回合额度、人物卡或审计表。
2. **所有机械结算继续调用确定性 Python 代码**。模型不得自行计算骰点、伤害、法术位或回合消耗。
3. **身份和权限由适配层注入**。不要让模型自行填写 `sender_id`、`is_dm` 或任意 `campaign_id`。
4. **先旁路、再替换**。迁移期间保留现有 FastAPI、Next.js 和 NapCat 回调作为回滚路径。

## 2. 当前能力与 nanobot 的映射

| DM_agent 能力 | 当前实现 | nanobot 中的归属 | 迁移策略 |
|---|---|---|---|
| LLM 工具循环 | `llm_loop.py`、`llm.py` | `AgentLoop`、Provider、ToolRegistry | 由 nanobot 替换 |
| Lobby / DM / 骰娘 | `services.py`、`message_router.py`、`dice_assistant.py` | Skill + DND 上下文工具 | 保留模式状态，删除重复 LLM loop |
| 战役和人物卡 | SQLAlchemy models + services | 无对应领域能力 | 原样保留 |
| 战斗与回合额度 | `combat_tools.py`、`campaign_turns.py` | 无对应领域能力 | 原样保留并封装为工具 |
| 骰子、伤害、撤销和审计 | `checked_roll()`、审计表 | 无对应领域能力 | 原样保留，禁止用通用 shell 代替 |
| 规则 RAG / compendium | `rag/`、`RuleChunk`、`CompendiumEntry` | nanobot 有通用文件与搜索工具 | 仍用 DND 专用查询，后续再评估合并 |
| QQ / NapCat | HTTP callback + OneBot HTTP | 内置 `NapcatChannel`，Forward WebSocket | 最终切换到 nanobot 渠道 |
| QQ 与角色绑定 | `qq_bindings.py` | 无领域绑定模型 | 保留，但由渠道上下文驱动 |
| 文件解析 | `parsing/` | nanobot 已支持常见文档和媒体 | 人物卡 XLSX 保留专用解析；通用文档可逐步复用 |
| 人物卡 XLSX 导出 | `character_builder.py` | NapCat 支持发送本地媒体文件 | 工具返回文件路径，由渠道发送 |
| 后台内容生成 | `subagent_runner.py`、workflows | nanobot Subagent / long task | 分阶段替换，制品接受事务仍留在领域层 |
| 战役记忆压缩 | `memory_compressor.py` | nanobot session consolidation / auto compact | 两者用途不同；战役事实摘要保留，聊天压缩交给 nanobot |
| Next.js 管理 UI | `frontend/` | nanobot WebUI 是通用聊天 UI | 暂时并存，不要强行合并管理页面 |

## 3. 推荐的目标代码结构

不要直接修改 `D:\mcp\nanobot\nanobot\` 来塞入 DND 代码。建立独立可安装包，使 nanobot 可以继续跟随上游更新。

```text
D:\mcp\DM_agent\
  backend\
    app\
      domain\                 # 从现有模块逐步抽出的纯领域层
      db\                     # SQLAlchemy models/session
      adapters\
        nanobot_context.py    # RequestContext -> DndRequestContext
      tools\                  # 现有确定性业务函数
    tests\
  integrations\
    nanobot-dnd\
      pyproject.toml
      nanobot_dnd\
        __init__.py
        context.py
        tools\
          campaign.py
          character.py
          combat.py
          dice.py
          setting.py
          files.py
        skill\
          SKILL.md
      tests\
  docs\
```

建议把几十个细粒度旧工具整理成 6～10 个领域工具，每个工具通过 `action` 区分同类动作，例如 `dnd_combat(action="attack", ...)`。不要做成一个无约束的万能 `dnd(action, payload)`；过宽 schema 会降低模型选工具的准确率，也更难做权限控制。

## 4. 两阶段集成方案

### 4.1 过渡方案：双进程 sidecar

先保持 DM_agent 后端运行，把领域操作暴露为 MCP 或受保护的本地 API；nanobot 单独运行。这样可以规避当前依赖冲突：DM_agent 固定 `openai==1.82.0`、`pydantic-settings==2.9.1`，而 nanobot 需要 `openai>=2.8.0`、`pydantic>=2.12.0`。

```text
nanobot venv                         DM_agent backend venv
nanobot gateway  -- MCP/HTTP -->    DND domain adapter --> SQLite
```

优点是改动小、可快速验证；缺点是普通 MCP 调用不会自动携带 nanobot 的渠道身份。过渡期必须使用服务端签发的短期 `scope_token`，其中绑定：

- `channel`
- `chat_id`
- `sender_id`
- `campaign_id`
- `character_id`
- `is_dm`
- 过期时间与 nonce

领域服务验证 token 后再执行写操作。不要把以上字段作为模型可自由提供的参数。

### 4.2 最终方案：原生 Tool 插件

nanobot 已通过 Python entry point `nanobot.tools` 发现外部工具。每个 DND 工具继承 `nanobot.agent.tools.base.Tool`，在执行时读取 `current_request_context()`。

```python
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import current_request_context


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["status", "save", "pause", "resume"]}
    },
    "required": ["action"],
})
class DndCampaignTool(Tool):
    name = "dnd_campaign"
    description = "查询或管理当前 D&D 战役；写操作会检查 DM 权限。"

    async def execute(self, action: str) -> dict:
        request = current_request_context()
        scope = resolve_dnd_scope(request)  # 服务端解析，拒绝缺失或伪造身份
        return run_campaign_action(scope=scope, action=action)
```

插件包注册示例：

```toml
[project]
name = "nanobot-dnd"
version = "0.1.0"
dependencies = ["nanobot-ai", "dnd-dm-core"]

[project.entry-points."nanobot.tools"]
dnd_campaign = "nanobot_dnd.tools.campaign:DndCampaignTool"
dnd_character = "nanobot_dnd.tools.character:DndCharacterTool"
dnd_combat = "nanobot_dnd.tools.combat:DndCombatTool"
dnd_dice = "nanobot_dnd.tools.dice:DndDiceTool"
dnd_setting = "nanobot_dnd.tools.setting:DndSettingTool"
dnd_files = "nanobot_dnd.tools.files:DndFilesTool"
```

要进入同一 Python 环境，先将领域代码拆成 `dnd-dm-core`：它不应依赖 OpenAI SDK、FastAPI、LangGraph 或渠道 SDK，只保留 SQLAlchemy、规则、解析和确定性工具。生成类工作流可先继续走 sidecar，之后再改用 nanobot Subagent。

## 5. 必须补齐的渠道上下文

这是迁移中最重要的接口差异。

nanobot 的 `RequestContext` 有 `channel`、`chat_id`、`session_key` 和 `metadata`；当前内置 NapCat 渠道在群消息 metadata 中提供 `message_id`、`is_group`、`nickname`、`reply_to`，但**没有明确提供 `sender_id`**。而 DM_agent 的角色绑定、DM 权限和后台任务所有权都依赖真实 QQ 用户 ID。

在切流前应给 nanobot 增加一个很小、可上游合并的改动：

```python
metadata={
    "sender_id": str(user_id),
    "group_id": str(group_id) if message_type == "group" else None,
    "message_id": msg_id,
    "is_group": message_type == "group",
    "nickname": nickname,
    "reply_to": reply_to_id,
}
```

随后由适配层构造不可变上下文：

```python
@dataclass(frozen=True)
class DndRequestContext:
    platform: str
    chat_id: str
    sender_id: str
    campaign_id: str | None
    character_id: str | None
    is_dm: bool
    session_id: str
```

解析规则建议如下：

1. 从 `RequestContext` 读取渠道和真实用户，不接受模型输入。
2. 根据 `chat_id/group_id` 查询当前战役；不要再依赖全局单一 `NAPCAT_CAMPAIGN_ID`。
3. 用 `(campaign_id, sender_id)` 查询 `NapCatCharacterBinding`。
4. 用配置或数据库 ACL 判断 `is_dm`。
5. 工具执行前再次校验人物属于当前战役。
6. 每次写操作记录 actor、session、tool name 和参数摘要。

建议会话键采用：

```text
dnd:{campaign_id}:group:{group_id}          # 群战役公共叙事
dnd:{campaign_id}:private:{qq_user_id}      # 玩家私聊
dnd:lobby:private:{qq_user_id}              # 尚未选择战役
```

不要开启 nanobot 的 `unifiedSession`；它会把不同玩家和群聊合并到一个上下文，不适合多人跑团。

## 6. Skill 与 Prompt 迁移

将 `prompt_builder.py` 的叙事策略迁入 workspace skill，例如：

```text
<nanobot workspace>\skills\dnd-dm\SKILL.md
```

Skill 只描述行为规则：

- Lobby、DM、骰娘三种模式及进入/退出条件；
- 何时查询战役状态、人物热快照和规则；
- 机械结果必须来自工具；
- 信息不足时调用澄清动作，不擅自消耗回合额度；
- 只有 DM 可以发布设定、推进回合或接受后台制品；
- 隐藏信息和公开信息的输出边界。

不要把完整人物卡、战役事件或全部 5E 规则写进 Skill。它们应按需由工具查询，否则每轮都会浪费上下文，并可能产生过期状态。

建议关闭无关通用能力，至少在玩家可访问的实例中禁用 shell 和任意文件写入：

```json
{
  "tools": {
    "exec": {"enable": false},
    "restrictToWorkspace": true
  },
  "agents": {
    "defaults": {
      "unifiedSession": false,
      "disabledSkills": ["github", "tmux"]
    }
  }
}
```

## 7. nanobot 配置基线

先在 nanobot 自己的虚拟环境安装源码和插件：

```powershell
cd D:\mcp\nanobot
uv sync
uv pip install -e D:\mcp\DM_agent\integrations\nanobot-dnd
uv run nanobot plugins list
uv run nanobot onboard
```

`~/.nanobot/config.json` 的最小方向如下，字段名以当前 nanobot 配置为准：

```json
{
  "agents": {
    "defaults": {
      "timezone": "Asia/Singapore",
      "unifiedSession": false,
      "maxConcurrentSubagents": 2,
      "idleCompactAfterMinutes": 0
    }
  },
  "channels": {
    "napcat": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "accessToken": "${NAPCAT_TOKEN}",
      "allowFrom": ["允许的QQ号"],
      "groupPolicy": "mention",
      "welcomeNewMembers": false
    }
  },
  "tools": {
    "exec": {"enable": false},
    "restrictToWorkspace": true
  }
}
```

说明：

- nanobot NapCat 使用 **Forward WebSocket**，不是 DM_agent 当前的 HTTP Post callback。
- `groupPolicy: "mention"` 对应当前“群聊必须 @bot”的策略。
- `allowFrom: ["*"]` 会开放给所有 QQ 用户，不建议直接用于正式团。
- 为保留完整工具审计，先将 `idleCompactAfterMinutes` 设为 `0`；nanobot 文档说明空闲压缩会重写 session JSONL。战役审计仍以 DM 数据库为准。

## 8. 领域工具封装规则

### 8.1 工具输入输出

统一返回结构：

```json
{
  "ok": true,
  "narration": "对玩家可见的简短机械结果",
  "data": {},
  "media": [],
  "audit_id": "audit_xxx",
  "state_version": 42
}
```

- `narration` 可直接进入模型上下文；不要返回秘密 DM 数据。
- `media` 只允许领域层生成目录中的真实文件。
- `state_version` 用于检测并发回合操作。
- 失败必须返回稳定错误码，如 `NOT_DM`、`NO_CAMPAIGN`、`NOT_YOUR_TURN`、`STALE_STATE`，而不是只返回自然语言。

### 8.2 事务与并发

- 一次工具调用对应一个数据库事务。
- 战斗写操作锁定战役或检查 `state_version`，防止同一群多人同时出手造成丢失更新。
- `checked_roll()`、HP 修改、回合额度消耗和审计记录必须在同一事务边界内。
- “接受 AgentArtifact”继续保持单事务发布；nanobot Subagent 只生成草稿，不能直接绕过接受步骤写正式设定。

### 8.3 权限

工具按作用域分三类：

| 范围 | 示例 | 校验 |
|---|---|---|
| 所有人只读 | status、规则查询、法术查询 | 当前战役可见性 |
| 玩家写入 | 自己的检定、草稿、绑定角色动作 | sender 与 character 绑定 |
| DM 写入 | 发布设定、切战役、推进/结束战斗、接受制品 | `is_dm == true` |

不要仅依赖工具 description 中的“仅限 DM”；description 是给模型看的，权限必须在 Python handler 内强制执行。

## 9. 文件、WebUI 与后台任务

### 文件

nanobot 内置 NapCat 当前主要处理图片；DM_agent 还需要 XLSX、PDF、DOCX 等附件。迁移时应扩展 NapCat 渠道的 OneBot 文件段下载与发送，而不是退化为只支持图片。

完成标准：

- 群文件和私聊文件都能落到受限 media 目录；
- 文件大小、扩展名和 MIME 均校验；
- D&D 模板 XLSX 仍走 `parse_character_sheet_xlsx()`；
- 导出工具返回 XLSX 路径，渠道可将其作为 OneBot 文件发送；
- `last_attachments` 中只保存受控引用，不保存任意本地路径。

### WebUI

nanobot WebUI 先作为聊天和 session 调试入口；原 Next.js UI 继续负责战役、人物卡、时间线、设定草稿、冲突和审计管理。等核心切流稳定后，再决定是否：

1. 保留双 UI；或
2. 在 nanobot WebUI 增加 DND 专用面板；或
3. 将现有 Next.js 变成只调用领域 API 的管理控制台。

### 后台任务

先保留 `AgentJob`、`AgentArtifact`、`NotificationOutbox` 及 worker。第二阶段再把纯生成任务迁到 nanobot Subagent，但任务状态和审核制品仍落 DM 数据库。nanobot session 不是可靠任务队列。

## 10. 分步实施清单

### Phase 0：冻结基线

- [ ] 备份 `data/dm_agent.db`、角色模板和生成文件。
- [ ] 记录当前数据库 schema/version、健康检查和关键 API 输出。
- [ ] 跑完整 backend pytest，并单列当前已知失败，禁止把旧失败误算为迁移回归。
- [ ] 保存一套可重复的 QQ 测试剧本：建战役、绑角色、投骰、开战、攻击、撤销、导出。

### Phase 1：抽取领域内核

- [ ] 从 `services.py`、`message_router.py` 中移除对业务函数不必要的 LLM/渠道依赖。
- [ ] 建立 `DndRequestContext` 和统一 `resolve_scope()`。
- [ ] 将所有 handler 改为显式接收 scope，而不是读取环境全局或从消息文本推断身份。
- [ ] 保持现有 FastAPI 路由兼容，确保抽取没有改变行为。

### Phase 2：接入 nanobot Tool 插件

- [ ] 建立 `nanobot-dnd` 包和 entry points。
- [ ] 先迁移只读工具：status、规则、法术、人物热快照。
- [ ] 再迁移骰子和玩家动作。
- [ ] 最后迁移 DM 写操作、战斗推进、设定发布和制品接受。
- [ ] 用 Hook 或 `ToolCallAudit` 记录 nanobot tool name、参数摘要、结果和耗时。

### Phase 3：迁移 Prompt 与会话

- [ ] 创建 `dnd-dm` Skill。
- [ ] 为 lobby、群战役、玩家私聊建立独立 session key。
- [ ] 对照测试旧 `build_system_prompt()` 与新 Skill 的行为覆盖。
- [ ] 验证切换模式、切换战役后不会把旧战役的热数据带入新上下文。

### Phase 4：NapCat 灰度切流

- [ ] nanobot NapCat metadata 增加 `sender_id` 和 `group_id`。
- [ ] 补齐非图片文件接收和 XLSX 文件发送。
- [ ] 使用测试 QQ / 测试群，不与旧 callback 同时回复。
- [ ] 先只读影子运行：执行 scope 解析与工具选择但禁止写库，对比旧系统结果。
- [ ] 再按群切流；保留一键恢复旧 callback 的启动脚本。

### Phase 5：替换重复基础设施

- [ ] 删除或停用旧 `llm_loop.py` 与重复 Provider 配置。
- [ ] 评估用 nanobot Subagent 替换纯生成型 subagent。
- [ ] 保留领域记忆、任务制品、事务和审计模型。
- [ ] 稳定至少一个完整战役周期后，再清理旧 NapCat 回调和重复通用解析器。

## 11. 验收测试矩阵

| 场景 | 必须断言 |
|---|---|
| 群聊未 @ | `groupPolicy=mention` 时不回复 |
| 普通玩家调用 DM 工具 | 返回 `NOT_DM`，数据库不变 |
| 两个玩家同群出手 | 各自解析到正确角色，不串绑 |
| 切换战役 | session/scope/人物绑定同时切换，不读取旧战役热数据 |
| 骰子 | 结果来自 `checked_roll()` 且存在 DiceAuditLog |
| 攻击 | 命中、伤害、HP、回合额度和审计一致 |
| 并发攻击 | 一个成功，另一个重试或 `STALE_STATE`，无丢失更新 |
| 撤销伤害 | CharacterChange 可追溯，HP 精确恢复 |
| XLSX 导入 | 模板往返校验通过后才写正式角色 |
| XLSX 导出 | QQ 和 WebUI 都能得到可打开文件 |
| Subagent 生成 | 只生成待审核 artifact，未接受前不污染正式设定 |
| session 压缩 | 不影响数据库战役事实和工具权限 |
| 重启 | nanobot、worker、DM 数据库恢复后可继续同一战役 |

建议测试分层：

```powershell
# 领域回归
cd D:\mcp\DM_agent\backend
uv run pytest -q

# nanobot 自身回归
cd D:\mcp\nanobot
uv run pytest -q tests\tools tests\channels\test_napcat_channel.py

# 插件契约与端到端
cd D:\mcp\DM_agent\integrations\nanobot-dnd
uv run pytest -q
```

具体 NapCat 测试文件名应以 `rg --files tests | rg napcat` 的实际结果为准，不要把示例命令写死进 CI 前不验证。

## 12. 回滚方案

迁移期间保持以下开关：

```env
DND_AGENT_RUNTIME=legacy        # legacy | nanobot
DND_NANOBOT_WRITES=false        # 影子期禁止写
DND_NANOBOT_GROUP_ALLOWLIST=    # 仅灰度群
```

回滚步骤：

1. 停止 `nanobot gateway` 或禁用其 NapCat channel。
2. 恢复 NapCat HTTP Post URL 到 `http://127.0.0.1:8011/napcat/callback`。
3. 启动现有 `scripts\run_napcat_callback.bat`。
4. 检查 `/health`、`/integrations/status`、活动战役和绑定列表。
5. 如果灰度期间发生写入，基于 `ToolCallAudit`、`CharacterChange` 和 checkpoint 审核；不要直接覆盖数据库文件。

任何时候都只能有一个运行时负责同一 QQ 消息的最终回复和业务写入，否则会产生重复投骰、重复伤害或重复设定。

## 13. 不应迁移或删除的部分

以下能力不是 nanobot 的通用 memory/tool 可以等价替换的，应长期留在 DND 领域层：

- 战役、人物卡、绑定、设定、事件、实体和线索线程模型；
- HotSnapshot 与 active effects 计算；
- 回合动作额度、反应、移动和先攻状态机；
- `checked_roll()`、DiceAuditLog、CharacterChange 和撤销语义；
- 人物卡模板的结构化 XLSX 往返校验；
- AgentArtifact 接受/拒绝事务；
- 规则库和 compendium 的领域查询语义；
- DM/玩家可见性和 campaign-scoped ACL。

## 14. 建议的首个可交付里程碑

第一版不要追求一次替换全部 53+ 工具。建议把里程碑限定为：

1. nanobot WebUI/CLI 可使用 `dnd_campaign(status)` 查询现有 SQLite 战役；
2. 可按真实 QQ 用户解析角色绑定；
3. `dnd_dice(roll)` 和一次基础攻击完整写入现有审计表；
4. 普通玩家无法调用 DM 写工具；
5. 原 Next.js UI 能立即看到 nanobot 产生的状态变更；
6. 旧 NapCat callback 可在五分钟内恢复。

这个切片同时验证最关键的四条链路：nanobot 工具发现、渠道身份、数据库事务和旧 UI 兼容。完成后再扩展人物卡、设定编辑、附件和 Subagent，风险最低。
