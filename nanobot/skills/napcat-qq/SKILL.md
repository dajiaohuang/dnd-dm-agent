---
name: napcat-qq
description: "通过 NanoBot 内置 NapCat（OneBot v11）渠道处理 QQ 私聊、群聊和媒体消息，并使用规范的目标格式。"
metadata: {"nanobot":{"emoji":"🐧"}}
---

# NapCat QQ

使用 NanoBot 内置的 `napcat` 渠道连接 NapCat Forward WebSocket。不要调用其他 QQ
发送途径，也不要使用 OpenClaw 的 `session:napcat:*` sessionKey 格式。

## 目标格式

- QQ 私聊：`private:<QQ号>`
- QQ 群聊：`group:<群号>`
- 跨频道主动发送时，调用 `message` 工具并显式传入：
  - `channel: "napcat"`
  - `chat_id: "private:<QQ号>"` 或 `chat_id: "group:<群号>"`

纯数字不是合法的 NanoBot NapCat `chat_id`。用户只提供数字时，必须先确认它是 QQ
用户还是 QQ 群。用户仅提供昵称或群名时，应请求准确的 QQ 号或群号；当前渠道不提供
联系人模糊搜索工具。

## 回复与主动发送

- 正常回复当前 QQ 会话时，直接回答，不调用 `message` 工具。
- 只有用户明确要求跨会话发送、主动通知或发送现有附件时，才调用 `message`。
- 同一会话的默认上下文已经包含正确的 `channel` 和 `chat_id`，不要自行重建。

## 群聊规则

- `groupPolicy: "mention"`：仅在被 @ 或回复机器人消息时响应。
- `groupPolicy: "open"`：响应群内所有消息。
- `groupPolicyOverrides` 可按群号覆盖默认策略。
- 不得根据消息文本推断发送者身份。使用请求上下文中的 `sender_id`、`group_id` 和
  `chat_id`，并通过数据库 `channel_bindings` 解析战役角色。

## 媒体

- `message.media` 接收本地文件路径或 HTTP(S) URL。
- 当前 NapCat 渠道正式支持图片发送与图片入站下载。
- 不要声称已发送语音、压缩包或角色卡文件，除非渠道返回成功且对应媒体类型已实现。

## 配置基线

```json
{
  "channels": {
    "napcat": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "accessToken": "YOUR_WEBSOCKET_TOKEN",
      "allowFrom": ["*"],
      "groupPolicy": "mention",
      "welcomeNewMembers": true
    }
  }
}
```

生产环境不要长期使用 `allowFrom: ["*"]`；应填写允许访问机器人的 QQ 号。
