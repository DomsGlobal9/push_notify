"""Firebase Cloud Messaging HTTP v1.

FCM is used only as the pipe to Android (Google's OS-level push channel is the
only thing that can wake a closed Android app). The legacy multicast/batch APIs
are gone, so it is one request per token; HTTP/2 multiplexing over a pooled
client gives the throughput.

Auth: the service account signs a JWT, exchanged for a 1-hour OAuth token that
is cached and refreshed 5 minutes early, or immediately on a 401.
"""

import threading
import time

import httpx
import jwt

from . import SendResult, Target, parse_retry_after

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"


class FcmSender:
    provider = "fcm"

    def __init__(self, service_account: dict, client: httpx.Client | None = None):
        self.sa = service_account
        self.url = f"https://fcm.googleapis.com/v1/projects/{service_account['project_id']}/messages:send"
        self.client = client or httpx.Client(
            http2=True, timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=10),
        )
        self._token = None
        self._expiry = 0.0
        self._lock = threading.Lock()

    def close(self):
        self.client.close()

    # ---- auth ----
    def _access_token(self) -> str:
        if self._token and time.time() < self._expiry - 300:
            return self._token
        with self._lock:
            if self._token and time.time() < self._expiry - 300:
                return self._token
            now = int(time.time())
            token_uri = self.sa.get("token_uri") or DEFAULT_TOKEN_URI
            headers = {"kid": self.sa["private_key_id"]} if self.sa.get("private_key_id") else None
            assertion = jwt.encode(
                {"iss": self.sa["client_email"], "scope": SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600},
                self.sa["private_key"], algorithm="RS256", headers=headers,
            )
            resp = self.client.post(token_uri, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
            resp.raise_for_status()
            body = resp.json()
            self._token = body["access_token"]
            self._expiry = time.time() + int(body.get("expires_in", 3600))
            return self._token

    # ---- payload ----
    @staticmethod
    def build(target: Target, message: dict, notification_id: str) -> dict:
        data = {**(message.get("data") or {}), "_ngw_id": notification_id}
        msg: dict = {"token": target.token, "data": data}
        title, body, image = message.get("title"), message.get("body"), message.get("image")
        has_alert = bool(title or body)
        if has_alert:
            msg["notification"] = {k: v for k, v in (("title", title), ("body", body), ("image", image)) if v}

        ttl = int(message.get("ttl_seconds", 86400))
        high = message.get("priority", "high") == "high"
        if target.platform == "android":
            android: dict = {"priority": "HIGH" if high else "NORMAL", "ttl": f"{ttl}s"}
            if message.get("collapse_key"):
                android["collapse_key"] = message["collapse_key"]
            if has_alert:
                an = {}
                channel = message.get("android_channel_id") or target.android_channel_id
                if channel:
                    an["channel_id"] = channel
                if message.get("sound"):
                    an["sound"] = message["sound"]
                if an:
                    android["notification"] = an
            msg["android"] = android
        else:  # an iOS app that takes its tokens from FCM
            aps: dict = {}
            if has_alert and message.get("sound"):
                aps["sound"] = message["sound"]
            if message.get("badge") is not None:
                aps["badge"] = message["badge"]
            if image:
                aps["mutable-content"] = 1
            if message.get("content_available"):
                aps["content-available"] = 1
            headers = {"apns-priority": "10" if (has_alert and high) else "5",
                       "apns-expiration": str(int(time.time()) + ttl if ttl else 0)}
            if message.get("collapse_key"):
                headers["apns-collapse-id"] = message["collapse_key"]
            msg["apns"] = {"headers": headers, "payload": {"aps": aps}}
        return {"message": msg}

    # ---- send ----
    def send(self, target: Target, message: dict, notification_id: str) -> SendResult:
        payload = self.build(target, message, notification_id)
        for _ in range(2):
            try:
                token = self._access_token()
                resp = self.client.post(self.url, json=payload, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 429 or code >= 500:
                    return SendResult("retry", code, "Google OAuth endpoint unavailable")
                return SendResult("failed", code, f"Service account rejected: {exc.response.text[:200]}")
            except (jwt.PyJWTError, ValueError, KeyError) as exc:
                return SendResult("failed", None, f"Bad service account: {exc}")
            except httpx.HTTPError as exc:
                return SendResult("retry", None, f"network: {type(exc).__name__}")
            if resp.status_code == 200:
                return SendResult("sent", 200)
            if resp.status_code == 401:
                self._token = None
                continue
            return self.classify(resp.status_code, resp)
        return SendResult("retry", 401, "UNAUTHENTICATED after token refresh")

    @staticmethod
    def classify(code: int, resp: httpx.Response) -> SendResult:
        try:
            err = resp.json().get("error", {})
        except ValueError:
            err = {}
        message = (err.get("message") or "")[:300]
        fcm_code = next((d.get("errorCode") for d in err.get("details", []) if d.get("errorCode")),
                        err.get("status", ""))
        reason = f"{fcm_code}: {message}" if fcm_code else message
        if fcm_code in ("UNREGISTERED", "SENDER_ID_MISMATCH") or code == 404:
            return SendResult("invalid_token", code, reason)
        if fcm_code == "INVALID_ARGUMENT" and "registration token" in message.lower():
            return SendResult("invalid_token", code, reason)
        if code == 429 or code >= 500 or fcm_code in ("QUOTA_EXCEEDED", "UNAVAILABLE", "INTERNAL"):
            return SendResult("retry", code, reason, parse_retry_after(resp.headers.get("retry-after")))
        return SendResult("failed", code, reason)
