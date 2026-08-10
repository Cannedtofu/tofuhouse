import logging
import time

import requests

import config

logger = logging.getLogger(__name__)


class WeComAPIError(Exception):
    def __init__(self, message, errcode=None, response=None):
        super().__init__(message)
        self.errcode = errcode
        self.response = response


class WeComClient:
    TOKEN_INVALID_ERRCODES = {40001, 40014, 42001}

    def __init__(self, corp_id, agent_id, secret, base_url=None, timeout=None, session=None):
        self.corp_id = corp_id
        self.agent_id = int(agent_id)
        self.secret = secret
        self.base_url = (base_url or config.WECOM_API_BASE_URL).rstrip("/")
        self.timeout = timeout or config.WECOM_TIMEOUT_SECONDS
        self.session = session or requests.Session()
        self._access_token = None
        self._expires_at = 0

    @classmethod
    def from_config(cls):
        cls._require_config("WECOM_CORP_ID", config.WECOM_CORP_ID)
        cls._require_config("WECOM_AGENT_ID", config.WECOM_AGENT_ID)
        cls._require_config("WECOM_SECRET", config.WECOM_SECRET)
        return cls(
            corp_id=config.WECOM_CORP_ID,
            agent_id=config.WECOM_AGENT_ID,
            secret=config.WECOM_SECRET,
        )

    @staticmethod
    def _require_config(name, value):
        if not value:
            raise WeComAPIError(f"{name} is not configured")

    def get_access_token(self, force_refresh=False):
        now = time.time()
        if not force_refresh and self._access_token and now < self._expires_at:
            return self._access_token

        url = f"{self.base_url}/cgi-bin/gettoken"
        data = self._request_json(
            "GET",
            url,
            params={"corpid": self.corp_id, "corpsecret": self.secret},
        )

        access_token = data.get("access_token")
        expires_in = int(data.get("expires_in", 7200))
        if not access_token:
            raise WeComAPIError("WeCom token response did not include access_token", response=data)

        self._access_token = access_token
        self._expires_at = now + max(expires_in - 300, 60)
        logger.info("Refreshed WeCom access token; expires in %s seconds", expires_in)
        return self._access_token

    def send_text(self, user_id, content):
        return self._send_message(
            {
                "touser": user_id,
                "msgtype": "text",
                "agentid": self.agent_id,
                "text": {"content": content},
            }
        )

    def send_markdown(self, user_id, content):
        raise NotImplementedError("WeCom markdown messages are reserved for a later phase")

    def send_news(self, user_id, articles):
        raise NotImplementedError("WeCom news messages are reserved for a later phase")

    def send_image(self, user_id, media_id):
        raise NotImplementedError("WeCom image messages are reserved for a later phase")

    def _send_message(self, payload):
        token = self.get_access_token()
        try:
            return self._post_message(token, payload)
        except WeComAPIError as exc:
            if exc.errcode not in self.TOKEN_INVALID_ERRCODES:
                raise
            logger.warning("WeCom token invalid or expired; refreshing and retrying once")
            token = self.get_access_token(force_refresh=True)
            return self._post_message(token, payload)

    def _post_message(self, token, payload):
        url = f"{self.base_url}/cgi-bin/message/send"
        return self._request_json("POST", url, params={"access_token": token}, json=payload)

    def _request_json(self, method, url, **kwargs):
        try:
            response = self.session.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("WeCom HTTP request failed")
            raise WeComAPIError(f"WeCom HTTP request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise WeComAPIError("WeCom response was not valid JSON") from exc

        errcode = data.get("errcode", 0)
        if errcode != 0:
            errmsg = data.get("errmsg", "")
            raise WeComAPIError(f"WeCom API error {errcode}: {errmsg}", errcode=errcode, response=data)

        return data
