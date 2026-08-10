# Enterprise WeCom Assistant

这是 News Agent 仓库里的一个独立子项目，用来承载企业微信自建应用相关能力。

第一阶段只做一件事：服务器端程序通过企业微信官方 API，主动向指定企业微信成员推送应用消息。

暂不实现：接收企业微信消息、callback URL、Token/EncodingAESKey 校验、AES 解密、会话状态或 Agent 对话路由。

## 边界

子项目负责：

- WeCom API adapter
- notification abstraction
- token cache
- outbound text push
- WeCom 环境变量说明
- 服务器部署和验收步骤

主业务负责：

- 新闻监控
- 财报/会议/公司研究 workflow
- LLM 处理
- 决定何时调用 `notify(...)`

主业务代码不要直接调用企业微信 HTTP API，统一走通知层。

## 当前代码入口

- `notifications/wecom.py`: 企业微信 API client
- `notifications/base.py`: channel-neutral notification service
- `scripts/test_wecom.py`: 真实凭据配置后的主动推送验收脚本
- `.env.example`: WeCom 环境变量模板
- `Dockerfile` / `docker-compose.yml`: 可选 Docker 部署入口

## 环境变量

正式企业微信企业和自建应用创建后，在 `.env` 填入：

```env
WECOM_CORP_ID=
WECOM_AGENT_ID=
WECOM_SECRET=
WECOM_TARGET_USER=
WECOM_TIMEOUT_SECONDS=10
```

不要把真实 Secret 写进 Python 文件、README、提交记录或聊天消息。

## 调用方式

```python
from notifications import NotificationService

NotificationService.from_config().notify(
    channel="wecom",
    title="Jensen Huang 新访谈",
    content=summary,
)
```

## 第一阶段验收

配置完成后运行：

```bash
python scripts/test_wecom.py
```

期望收到企业微信应用消息：

```text
服务器测试

企业微信主动推送成功
```
