import logging

import config

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    pass


class NotificationService:
    def __init__(self, wecom_client=None):
        self.wecom_client = wecom_client

    @classmethod
    def from_config(cls):
        from .wecom import WeComClient

        wecom_client = None
        if config.WECOM_CORP_ID and config.WECOM_AGENT_ID and config.WECOM_SECRET:
            wecom_client = WeComClient.from_config()
        return cls(wecom_client=wecom_client)

    def notify(self, channel, title, content, user_id=None):
        if channel != "wecom":
            raise NotificationError(f"Unsupported notification channel: {channel}")
        if not self.wecom_client:
            raise NotificationError("WeCom is not configured")

        target_user = user_id or config.WECOM_TARGET_USER
        if not target_user:
            raise NotificationError("WECOM_TARGET_USER is not configured")

        message = self._format_text(title, content)
        logger.info("Sending WeCom notification to %s: %s", target_user, title)
        return self.wecom_client.send_text(target_user, message)

    @staticmethod
    def _format_text(title, content):
        title = (title or "").strip()
        content = (content or "").strip()
        if title and content:
            return f"{title}\n\n{content}"
        return title or content
