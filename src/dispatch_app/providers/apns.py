"""Apple Push Notification service, direct, over HTTP/2 with a .p8 token key.

One .p8 key serves every app in the Apple team; the bundle id travels per
request in `apns-topic`. Provider tokens (ES256 JWTs) are reused for 40 minutes:
Apple rejects ones older than 60 and answers TooManyProviderTokenUpdates if a
new one is minted more often than every 20.

Sandbox vs production is a property of the *token* (debug vs release build), so
it is stored per device and picks the host per request.
"""

import json
import threading
import time

import httpx
import jwt

from . import SendResult, Target, parse_retry_after

HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}
MAX_PAYLOAD = 4096
TOKEN_REUSE_SECONDS = 40 * 60
MIN_REISSUE_SECONDS = 20 * 60

INVALID_TOKEN_REASONS = {"BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered"}
RETRY_REASONS = {"TooManyRequests", "InternalServerError", "ServiceUnavailable", "Shutdown"}


class ApnsSender:
    provider = "apns"

    def __init__(self, secret: dict, client: httpx.Client | None = None):
        self.team_id = secret["team_id"]
        self.key_id = secret["key_id"]
        self.p8 = secret["p8"]
        self.client = client or httpx.Client(http2=True, timeout=httpx.Timeout(15.0, connect=10.0))
        self._token = None
        self._issued_at = 0.0
        self._lock = threading.Lock()

    def close(self):
        self.client.close()

    def _provider_token(self, force: bool = False) -> str:
        max_age = MIN_REISSUE_SECONDS if force else TOKEN_REUSE_SECONDS
        if self._token and time.time() - self._issued_at < max_age:
            return self._token
        with self._lock:
            if self._token and time.time() - self._issued_at < max_age:
                return self._token
            now = int(time.time())
            self._token = jwt.encode({"iss": self.team_id, "iat": now}, self.p8, algorithm="ES256",
                                     headers={"kid": self.key_id})
            self._issued_at = now
            return self._token

    @staticmethod
    def build(target: Target, message: dict, notification_id: str) -> tuple[dict, dict]:
        title, body, image = message.get("title"), message.get("body"), message.get("image")
        has_alert = bool(title or body)
        aps: dict = {}
        if has_alert:
            aps["alert"] = {k: v for k, v in (("title", title), ("body", body)) if v}
            if message.get("sound"):
                aps["sound"] = message["sound"]
        if message.get("badge") is not None:
            aps["badge"] = message["badge"]
        if image:
            aps["mutable-content"] = 1  # lets a Notification Service Extension attach the image
        if message.get("content_available"):
            aps["content-available"] = 1

        payload = {**(message.get("data") or {}), "aps": aps, "_ngw_id": notification_id}
        if image:
            payload["image"] = image

        ttl = int(message.get("ttl_seconds", 86400))
        headers = {
            "apns-topic": target.bundle_id,
            "apns-push-type": "alert" if has_alert else "background",
            "apns-priority": "10" if has_alert and message.get("priority", "high") == "high" else "5",
            "apns-expiration": str(int(time.time()) + ttl if ttl else 0),
        }
        if message.get("collapse_key"):
            headers["apns-collapse-id"] = message["collapse_key"]
        return headers, payload

    def send(self, target: Target, message: dict, notification_id: str) -> SendResult:
        headers, payload = self.build(target, message, notification_id)
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if len(body) > MAX_PAYLOAD:
            return SendResult("failed", None, f"Payload is {len(body)} bytes; APNs allows 4096")
        url = f"{HOSTS.get(target.apns_environment, HOSTS['production'])}/3/device/{target.token}"

        for attempt in range(2):
            try:
                token = self._provider_token(force=attempt > 0)
                resp = self.client.post(url, content=body, headers={**headers, "authorization": f"bearer {token}"})
            except (jwt.PyJWTError, ValueError) as exc:
                return SendResult("failed", None, f"Bad .p8 key: {exc}")
            except httpx.HTTPError as exc:
                return SendResult("retry", None, f"network: {type(exc).__name__}")
            if resp.status_code == 200:
                return SendResult("sent", 200)
            try:
                reason = resp.json().get("reason", "")
            except ValueError:
                reason = resp.text[:200]
            if reason == "ExpiredProviderToken" and attempt == 0:
                self._issued_at = 0  # force a fresh token on the second pass
                continue
            return self.classify(resp.status_code, reason, parse_retry_after(resp.headers.get("retry-after")))
        return SendResult("retry", 403, "ExpiredProviderToken")

    @staticmethod
    def classify(code: int, reason: str, retry_after: float | None) -> SendResult:
        if code == 410 or reason in INVALID_TOKEN_REASONS:
            return SendResult("invalid_token", code, reason)
        if code == 429 or code >= 500 or reason in RETRY_REASONS:
            return SendResult("retry", code, reason, retry_after)
        return SendResult("failed", code, reason)
