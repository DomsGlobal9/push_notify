"""FCM and APNs request building and response classification, against a mock HTTP transport."""

import json

import httpx
import jwt
from django.test import SimpleTestCase

from src.core.testing import apns_secret, fcm_secret
from src.dispatch_app.providers import Target
from src.dispatch_app.providers.apns import ApnsSender
from src.dispatch_app.providers.fcm import FcmSender

ANDROID = Target(1, "fcm-token", "android", "com.example.shop", "production", "orders")
IOS = Target(2, "abc123", "ios", "com.example.shop.ios", "sandbox", "")
MSG = {"title": "Ready", "body": "Pick up today", "data": {"order_id": "7"}, "sound": "default",
       "priority": "high", "ttl_seconds": 3600, "image": "https://x/y.png", "collapse_key": "order-7"}


def mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class FcmTests(SimpleTestCase):
    def make(self, send_handler, token_calls=None):
        def handler(request):
            if request.url.host == "oauth2.googleapis.com":
                if token_calls is not None:
                    token_calls.append(1)
                return httpx.Response(200, json={"access_token": "ya29.token", "expires_in": 3600})
            return send_handler(request)
        return FcmSender(fcm_secret(), client=mock(handler))

    def test_android_payload(self):
        body = FcmSender.build(ANDROID, MSG, "nid-1")["message"]
        self.assertEqual(body["token"], "fcm-token")
        self.assertEqual(body["data"], {"order_id": "7", "_ngw_id": "nid-1"})
        self.assertEqual(body["notification"]["image"], "https://x/y.png")
        self.assertEqual(body["android"]["priority"], "HIGH")
        self.assertEqual(body["android"]["ttl"], "3600s")
        self.assertEqual(body["android"]["collapse_key"], "order-7")
        self.assertEqual(body["android"]["notification"]["channel_id"], "orders")

    def test_ios_via_fcm_gets_apns_block(self):
        body = FcmSender.build(IOS, MSG, "nid-1")["message"]
        self.assertEqual(body["apns"]["payload"]["aps"]["mutable-content"], 1)
        self.assertEqual(body["apns"]["headers"]["apns-priority"], "10")

    def test_sends_with_bearer_and_caches_token(self):
        seen, token_calls = [], []

        def send(request):
            seen.append(request.headers["authorization"])
            self.assertIn("/projects/demo-project/messages:send", str(request.url))
            return httpx.Response(200, json={"name": "projects/x/messages/1"})
        s = self.make(send, token_calls)
        self.assertEqual(s.send(ANDROID, MSG, "n").outcome, "sent")
        self.assertEqual(s.send(ANDROID, MSG, "n").outcome, "sent")
        self.assertEqual(seen, ["Bearer ya29.token"] * 2)
        self.assertEqual(len(token_calls), 1)

    def test_assertion_is_signed_by_the_service_account(self):
        captured = {}

        def handler(request):
            if request.url.host == "oauth2.googleapis.com":
                form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
                captured["claims"] = jwt.decode(form["assertion"], options={"verify_signature": False})
                return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
            return httpx.Response(200, json={})
        FcmSender(fcm_secret(), client=mock(handler)).send(ANDROID, MSG, "n")
        self.assertEqual(captured["claims"]["iss"], "push@demo.iam.gserviceaccount.com")
        self.assertIn("firebase.messaging", captured["claims"]["scope"])

    def test_401_refreshes_token_once(self):
        calls, token_calls = [], []

        def send(request):
            calls.append(1)
            return httpx.Response(401 if len(calls) == 1 else 200, json={})
        s = self.make(send, token_calls)
        self.assertEqual(s.send(ANDROID, MSG, "n").outcome, "sent")
        self.assertEqual(len(token_calls), 2)

    def fcm_error(self, code, status, error_code=None, message="x", headers=None):
        err = {"code": code, "status": status, "message": message, "details": []}
        if error_code:
            err["details"].append({"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                                   "errorCode": error_code})
        return self.make(lambda r: httpx.Response(code, json={"error": err}, headers=headers or {})).send(
            ANDROID, MSG, "n")

    def test_classification(self):
        self.assertEqual(self.fcm_error(404, "NOT_FOUND", "UNREGISTERED").outcome, "invalid_token")
        self.assertEqual(self.fcm_error(403, "PERMISSION_DENIED", "SENDER_ID_MISMATCH").outcome, "invalid_token")
        self.assertEqual(self.fcm_error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT",
                                        "The registration token is not a valid FCM registration token"
                                        ).outcome, "invalid_token")
        self.assertEqual(self.fcm_error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT", "bad ttl").outcome, "failed")
        r = self.fcm_error(429, "RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED", headers={"retry-after": "30"})
        self.assertEqual((r.outcome, r.retry_after), ("retry", 30.0))
        self.assertEqual(self.fcm_error(503, "UNAVAILABLE", "UNAVAILABLE").outcome, "retry")
        self.assertEqual(self.fcm_error(401, "UNAUTHENTICATED", "THIRD_PARTY_AUTH_ERROR").outcome, "retry")

    def test_network_error_is_retry(self):
        def boom(request):
            raise httpx.ConnectError("down")
        self.assertEqual(FcmSender(fcm_secret(), client=mock(boom)).send(ANDROID, MSG, "n").outcome, "retry")

    def test_rejected_service_account_is_permanent(self):
        def handler(request):
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.assertEqual(FcmSender(fcm_secret(), client=mock(handler)).send(ANDROID, MSG, "n").outcome, "failed")


class ApnsTests(SimpleTestCase):
    def make(self, handler):
        return ApnsSender(apns_secret(), client=mock(handler))

    def test_alert_payload_and_headers(self):
        headers, payload = ApnsSender.build(IOS, MSG, "nid-1")
        self.assertEqual(headers["apns-topic"], "com.example.shop.ios")
        self.assertEqual(headers["apns-push-type"], "alert")
        self.assertEqual(headers["apns-priority"], "10")
        self.assertEqual(headers["apns-collapse-id"], "order-7")
        self.assertEqual(payload["aps"]["alert"], {"title": "Ready", "body": "Pick up today"})
        self.assertEqual(payload["aps"]["mutable-content"], 1)
        self.assertEqual((payload["order_id"], payload["_ngw_id"]), ("7", "nid-1"))

    def test_background_push_uses_priority_5(self):
        headers, payload = ApnsSender.build(IOS, {"data": {"sync": "1"}, "content_available": True}, "n")
        self.assertEqual((headers["apns-push-type"], headers["apns-priority"]), ("background", "5"))
        self.assertEqual(payload["aps"], {"content-available": 1})

    def test_sends_to_the_device_environment_with_es256_token(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            token = request.headers["authorization"].split(" ", 1)[1]
            seen["header"] = jwt.get_unverified_header(token)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200)
        self.assertEqual(self.make(handler).send(IOS, MSG, "n").outcome, "sent")
        self.assertEqual(seen["url"], "https://api.sandbox.push.apple.com/3/device/abc123")
        self.assertEqual((seen["header"]["alg"], seen["header"]["kid"]), ("ES256", "KEY1234567"))

    def test_classification(self):
        def answer(code, reason, headers=None):
            return self.make(lambda r: httpx.Response(code, json={"reason": reason}, headers=headers or {})).send(
                IOS, MSG, "n")
        self.assertEqual(answer(410, "Unregistered").outcome, "invalid_token")
        self.assertEqual(answer(400, "BadDeviceToken").outcome, "invalid_token")
        self.assertEqual(answer(400, "DeviceTokenNotForTopic").outcome, "invalid_token")
        self.assertEqual(answer(400, "PayloadTooLarge").outcome, "failed")
        self.assertEqual(answer(429, "TooManyRequests").outcome, "retry")
        self.assertEqual(answer(503, "ServiceUnavailable").outcome, "retry")
        self.assertEqual(answer(403, "InvalidProviderToken").outcome, "failed")

    def test_expired_provider_token_retries_once_with_fresh_token(self):
        tokens = []

        def handler(request):
            tokens.append(request.headers["authorization"])
            if len(tokens) == 1:
                return httpx.Response(403, json={"reason": "ExpiredProviderToken"})
            return httpx.Response(200)
        self.assertEqual(self.make(handler).send(IOS, MSG, "n").outcome, "sent")
        self.assertEqual(len(tokens), 2)

    def test_oversized_payload_rejected_before_sending(self):
        big = {**MSG, "data": {"blob": "x" * 5000}}
        r = self.make(lambda r: httpx.Response(200)).send(IOS, big, "n")
        self.assertEqual(r.outcome, "failed")
        self.assertIn("4096", r.reason)
