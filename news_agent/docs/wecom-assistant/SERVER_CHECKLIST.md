# Server Checklist

拿到服务器连接方式后，第一步只检查状态，不立刻改服务器。

## 需要的连接信息

请提供以下信息即可，不要提供企业微信 Secret：

```text
host=
ssh_port=
ssh_user=
ssh_key_path=   # 如果使用密钥
project_path=   # 如果服务器上已经有目录
```

如果只能用密码登录，可以先只给 host、port、user；密码不要发在聊天里，等 SSH 命令交互提示时输入。

## 只读检查命令

登录服务器后运行：

```bash
uname -a
cat /etc/os-release
docker --version
python3 --version
git --version
curl -4 ifconfig.me
pwd
ls -la
```

检查目标：

- 确认 OS / Linux distribution
- 确认 Docker 是否安装
- 确认 Python 版本
- 确认 Git 是否安装
- 确认公网出口 IPv4
- 确认当前项目目录状态

## 部署方式选择

优先 Docker，当满足：

- Docker 已安装或可以安装
- 服务器公网出口 IPv4 稳定
- 可以开放/反代容器端口
- 后续希望模块和依赖更容易迁移

优先直接 Python + systemd，当满足：

- 服务器已经按现有 News Agent 方式运行
- 已经有 `.venv`、gunicorn、systemd service
- 不想改变现有部署方式

## WeCom 可信 IP

正式企业微信自建应用创建后，把服务器的公网出口 IPv4 填入企业微信自建应用的「企业可信 IP」。

本地电脑公网 IP 不要填。以服务器上 `curl -4 ifconfig.me` 的结果为准。
