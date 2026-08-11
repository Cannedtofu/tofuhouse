import logging
import os
from urllib.parse import parse_qs, urlparse, urlunparse

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
        return self._post(self.webhook_url, json=payload)

    def upload_file(self, file_path):
        upload_url = self._upload_url()
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as handle:
                response = self.session.post(
                    upload_url,
                    files={"media": (file_name, handle)},
                    timeout=self.timeout,
                )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("WeCom webhook file upload failed")
            raise WeComWebhookError(f"WeCom webhook file upload failed: {exc}") from exc

        data = self._json_response(response)
        media_id = data.get("media_id")
        if not media_id:
            raise WeComWebhookError("WeCom webhook upload response did not include media_id", response=data)
        return media_id

    def send_file(self, media_id):
        payload = {
            "msgtype": "file",
            "file": {"media_id": media_id},
        }
        return self._post(self.webhook_url, json=payload)

    def send_file_path(self, file_path):
        media_id = self.upload_file(file_path)
        return self.send_file(media_id)

    def _upload_url(self):
        parsed = urlparse(self.webhook_url)
        key = parse_qs(parsed.query).get("key", [""])[0]
        if not key:
            raise WeComWebhookError("WeCom webhook URL does not include key")
        path = parsed.path.replace("/webhook/send", "/webhook/upload_media")
        query = f"key={key}&type=file"
        return urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))

    def _post(self, url, **kwargs):
        try:
            response = self.session.post(url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("WeCom webhook HTTP request failed")
            raise WeComWebhookError(f"WeCom webhook HTTP request failed: {exc}") from exc

        return self._json_response(response)

    def _json_response(self, response):
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
