# Handover 2026-06-19

## 本次完成

### Channel 配置
- ✅ **WebSocket (WebUI)**: `ws://127.0.0.1:18765` — 端口 18765（8765 有 Windows 权限冲突）
- ✅ **Telegram**: `@dmMinthBbot` — polling 模式
- ✅ **NapCat QQ**: `ws://127.0.0.1:3001` — 便携 QQ v9.9.26-44498，已登录 `1534055688` (tc130精神扰乱装置)

### NapCat QQ 集成
- NapCat 运行时内置在 `localqq/`（不入 git）
- 安装脚本: `scripts/setup-napcat.ps1`（自动下载 + 配置）
- 启动脚本: `scripts/start-all.ps1`（`-Quick` 免扫码，`-NoQQ` 仅 gateway，`-CpuOnly` CPU 模式）
- 修复: `nanobot/channels/napcat.py` 覆写 `_handle_message`，群聊不经过 `allowFrom` 过滤

### 权限配置
```
私聊 allowFrom: ["2480933622"]
群 903107519: groupPolicy "mention"（需 @机器人）
Bot QQ: 1534055688
```

### 格式策略
- QQ: emoji + `【】` 强调，禁用 markdown bold/italic
- Telegram: 短段落，`**bold**` 少量使用
- WebUI: Markdown 完整支持
- 模板: `nanobot/templates/agent/identity.md`（按 channel 分支）

### 上下文优化
```
maxMessages:          120 → 40
consolidationRatio:  0.5 → 0.3
idleCompactAfterMin:  15 → 5
```

### 规则检索
- 英文 SRD 5.2.1: 20 sources / 2,763 sections / 2,684 chunks (BGE-M3 GPU)
- 中文 SRD 5.1: 8 sources / 1,944 sections / 1,864 chunks (BGE-M3 GPU)
- 新增 `ingest_directory_srd()` 方法 + CLI `rules ingest-zh-cn` 命令
- 中文 SRD 按类别合并（`localqq/../references/DND.SRD.zh-CN/merged/`）

### 战役管理
- `DM_RULES.md` §1.3: 创建新战役后必须立即创建初始 Snapshot
- `dnd-campaign-manager` SKILL: 创建战役后追加初始存档步骤

### 数据库
- SQLite: `~/.nanobot/dnd/dnd_dm.db` (70MB +)
- 引擎: deepseek-v4-flash @ 65536 context window
- BGE-M3: CUDA (RTX 5070 Ti)

## 启动方式

```powershell
# 完整启动
.\scripts\start-all.ps1

# 免扫码（已登录过）
.\scripts\start-all.ps1 -Quick

# 仅 gateway
.\scripts\start-all.ps1 -NoQQ
```

## 关键文件

| 文件 | 用途 |
|------|------|
| `~/.nanobot/config.json` | nanobot 主配置 |
| `localqq/` | NapCat + 便携 QQ（.gitignore） |
| `scripts/setup-napcat.ps1` | 一键安装 NapCat |
| `scripts/start-all.ps1` | 一键启动 |
| `nanobot/channels/napcat.py` | QQ 渠道（含群聊权限修复） |
| `nanobot/templates/agent/identity.md` | 按 channel 的格式策略 |
| `nanobot/dnd/rules/ingest.py` | 规则导入（中英双语） |

## 未完成

- 战役未创建（规则库已就绪，DM Agent 创建战役后自动建初始存档）
- `references/` 目录已 gitignore（含 DND.SRD.zh-CN 源文件）
