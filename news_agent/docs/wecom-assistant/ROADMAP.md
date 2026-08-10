# WeCom Assistant Roadmap

## Phase 1: Active Push

状态：代码骨架已准备，等待正式企业微信凭据和服务器可信 IP 验收。

包含：

- env-based configuration
- access token cache
- text message send
- timeout and HTTP status checks
- WeCom `errcode` checks
- token invalid/expired retry once
- acceptance script

验收命令：

```bash
python scripts/test_wecom.py
```

## Phase 2: Inbound Callback

暂不实现。等第一阶段跑通后再做。

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

## Longer Term

企业微信只是 notification channel。通知层以后可以继续接：

- WeCom
- Email
- Slack
- Telegram
- other channels

业务层保持类似：

```python
notify(channel="wecom", title="...", content="...")
```
