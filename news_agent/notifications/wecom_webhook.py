import logging

import requests

import config

logger = logging.getLogger(__name__)


class WeComWebhookError(Exception):
    def __init__(self, message, errcode=None, response=None):
        super().__init__(message)
        self.errcode = errcode
        self.response = response


class WeComWebhookClient:
    def __init__(self, webhook_url, timeout=None, session=None):
        self.webhook_url = webhook_url
        self.timeout = timeout or config.WECOM_TIMEOUT_SECONDS
        self.session = session or requests.Session()

    @classmethod
    def from_config(cls):
        if not config.WECOM_WEBHOOK_URL:
            raise WeComWebhookError("WECOM_WEBHOOK_URL is not configured")
        return cls(webhook_url=config.WECOM_WEBHOOK_URL)

    def send_text(self, content):
        payload = {
            "msgtype": "text",
            "text": {"content": content},
        }
        return self._post(payload)

    def _post(self, payload):
        try:
            response = self.session.post(self.webhook_url, json=payload, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("WeCom webhook HTTP request failed")
            raise WeComWebhookError(f"WeCom webhook HTTP request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise WeComWebhookError("WeCom webhook response was not valid JSON") from exc

        errcode = data.get("errcode", 0)
        if errcode != 0:
            errmsg = data.get("errmsg", "")
            raise WeComWebhookError(
                f"WeCom webhook API error {errcode}: {errmsg}",
                errcode=errcode,
                response=data,
            )

        return data
