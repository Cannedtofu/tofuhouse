# WeCom Assistant Roadmap

## Phase 1: Group Robot Webhook

状态：当前推荐路线。避免可信域名、备案、可信 IP 等配置成本，先让服务器主动推送跑通。

包含：

- env-based webhook URL configuration
- text message send
- timeout and HTTP status checks
- WeCom webhook `errcode` checks
- acceptance script

验收命令：

```bash
.venv/bin/python scripts/test_wecom_webhook.py
```

## Phase 2: Use Webhook From Workflows

在新闻监控、财报监控、会议提醒等 workflow 中接入：

```python
notify(channel="wecom_webhook", title="...", content="...")
```

## Phase 3: Self-Built App API

暂缓。等确实需要私聊指定成员、OAuth、工作台应用或双向对话时再做。

已有基础：

- access token cache
- self-built app text message send
- timeout and HTTP status checks
- WeCom `errcode` checks
- token invalid/expired retry once
- acceptance script

## Phase 4: Inbound Callback

暂不实现。

未来范围：

- `/wecom/callback`
- URL verification
- Token / EncodingAESKey
- signature verification
- AES decrypt/encrypt
- inbound message routing
- Agent / LLM processing
- reply message sending
- conversation state
- authentication and authorization
