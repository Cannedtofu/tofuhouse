# Enterprise WeCom Assistant

这是 News Agent 仓库里的一个独立子项目，用来承载企业微信相关通知能力。

当前推荐路线是企业微信群机器人 Webhook：服务器直接向一个企业微信群推送消息，不需要域名、备案、可信 IP、CorpID、AgentId 或 Secret。

自建应用官方 API 代码仍保留为长期路线，但由于可信域名、可信 IP、备案等配置成本较高，暂时不作为第一条验收路径。

## 当前边界

子项目负责：

- notification abstraction
- WeCom group robot webhook adapter
- optional WeCom self-built app API adapter
- env-based secret management
- outbound text push
- deployment and acceptance scripts

主业务负责：

- 新闻监控
- 财报/会议/公司研究 workflow
- LLM 处理
- 决定何时调用 `notify(...)`

主业务代码不要直接调用企业微信 HTTP API，统一走通知层。

## 当前代码入口

- `notifications/wecom_webhook.py`: 企业微信群机器人 webhook client
- `notifications/wecom.py`: 自建应用 API client，长期路线保留
- `notifications/base.py`: channel-neutral notification service
- `scripts/test_wecom_webhook.py`: 群机器人 webhook 验收脚本
- `scripts/test_wecom.py`: 自建应用 API 验收脚本
- `.env.example`: WeCom 环境变量模板

## Webhook 配置

在企业微信群中添加群机器人，复制 webhook URL，然后在服务器 `.env` 填入：

```env
WECOM_WEBHOOK_URL=
```

Webhook URL 是 secret，不要写进代码、README、提交记录或聊天消息。

## Webhook 调用方式

```python
from notifications import NotificationService

NotificationService.from_config().notify(
    channel="wecom_webhook",
    title="Jensen Huang 新访谈",
    content=summary,
)
```

## Webhook 验收

配置完成后运行：

```bash
cd /opt/tofuhouse/news_agent
.venv/bin/python scripts/test_wecom_webhook.py
```

期望企业微信群收到：

```text
服务器测试

企业微信群机器人主动推送成功
```

## 自建应用长期路线

如果以后需要私聊指定成员、工作台应用、OAuth、接收消息 callback 或 Agent 双向对话，再回到自建应用官方 API 路线。

自建应用需要的环境变量：

```env
WECOM_CORP_ID=
WECOM_AGENT_ID=
WECOM_SECRET=
WECOM_TARGET_USER=
WECOM_TIMEOUT_SECONDS=10
```
