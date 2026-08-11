import logging

import config

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    pass


class NotificationService:
    def __init__(self, wecom_client=None, wecom_webhook_client=None):
        self.wecom_client = wecom_client
        self.wecom_webhook_client = wecom_webhook_client

    @classmethod
    def from_config(cls):
        from .wecom import WeComClient
        from .wecom_webhook import WeComWebhookClient

        wecom_client = None
        if config.WECOM_CORP_ID and config.WECOM_AGENT_ID and config.WECOM_SECRET:
            wecom_client = WeComClient.from_config()

        wecom_webhook_client = None
        if config.WECOM_WEBHOOK_URL:
            wecom_webhook_client = WeComWebhookClient.from_config()

        return cls(wecom_client=wecom_client, wecom_webhook_client=wecom_webhook_client)

    def notify(self, channel, title, content, user_id=None):
        message = self._format_text(title, content)

        if channel == "wecom_webhook":
            if not self.wecom_webhook_client:
                raise NotificationError("WECOM_WEBHOOK_URL is not configured")
            logger.info("Sending WeCom webhook notification: %s", title)
            return self.wecom_webhook_client.send_text(message)

        if channel == "wecom":
            if not self.wecom_client:
                raise NotificationError("WeCom is not configured")

            target_user = user_id or config.WECOM_TARGET_USER
            if not target_user:
                raise NotificationError("WECOM_TARGET_USER is not configured")

            logger.info("Sending WeCom notification to %s: %s", target_user, title)
            return self.wecom_client.send_text(target_user, message)

        raise NotificationError(f"Unsupported notification channel: {channel}")

    def notify_file(self, channel, file_path, title=None):
        if channel != "wecom_webhook":
            raise NotificationError(f"Unsupported file notification channel: {channel}")
        if not self.wecom_webhook_client:
            raise NotificationError("WECOM_WEBHOOK_URL is not configured")
        if title:
            self.notify(channel="wecom_webhook", title=title, content="")
        logger.info("Sending WeCom webhook file: %s", file_path)
        return self.wecom_webhook_client.send_file_path(file_path)

    @staticmethod
    def _format_text(title, content):
        title = (title or "").strip()
        content = (content or "").strip()
        if title and content:
            return f"{title}\n\n{content}"
        return title or content
