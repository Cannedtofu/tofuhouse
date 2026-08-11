import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from notifications import NotificationService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main():
    service = NotificationService.from_config()
    service.notify(
        channel="wecom_webhook",
        title="服务器测试",
        content="企业微信群机器人主动推送成功",
    )
    print("wecom_webhook_test_ok")


if __name__ == "__main__":
    main()
