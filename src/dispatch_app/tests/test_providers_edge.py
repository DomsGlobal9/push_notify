"""Provider worst cases: broken auth, garbage responses, timeouts, byte limits."""

import json
import time

import httpx
from django.test import SimpleTestCase

from src.core.testing import apns_secret, fcm_secret
from src.dispatch_app.providers import Target, parse_retry_after
from src.dispatch_app.providers.apns import ApnsSender
from src.dispatch_app.providers.fcm import FcmSender

ANDROID = Target(1, "fcm-token", "android", "com.example.shop", "production", "")
IOS = Target(2, "abc123", "ios", "com.example.shop.ios", "production", "")


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def oauth_ok(request):
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})


class FcmEdgeTests(SimpleTestCase):
    def fcm(self, send, oauth=oauth_ok, secret=None):
        def handler(request):
            return oauth(request) if request.url.host == "oauth2.googleapis.com" else send(request)
        return FcmSender(secret or fcm_secret(), client=client(handler))

    def test_oauth_endpoint_down_is_retry(self):
        s = self.fcm(lambda r: httpx.Response(200), oauth=lambda r: httpx.Response(503, text="down"))
        self.assertEqual(s.send(ANDROID, {"title": "t"}, "n").outcome, "retry")

    def test_oauth_rate_limited_is_retry(self):
        s = self.fcm(lambda r: httpx.Response(200), oauth=lambda r: httpx.Response(429))
        self.assertEqual(s.send(ANDROID, {"title": "t"}, "n").outcome, "retry")

    def test_401_twice_is_retry_not_loop(self):
        calls = []
        s = self.fcm(lambda r: calls.append(1) or httpx.Response(401, json={}))
        self.assertEqual(s.send(ANDROID, {"title": "t"}, "n").outcome, "retry")
        self.assertEqual(len(calls), 2)

    def test_non_json_error_bodies(self):
        self.assertEqual(self.fcm(lambda r: httpx.Response(502, text="<html>bad gateway")).send(
            ANDROID, {"title": "t"}, "n").outcome, "retry")
        self.assertEqual(self.fcm(lambda r: httpx.Response(400, text="nope")).send(
            ANDROID, {"title": "t"}, "n").outcome, "failed")

    def test_timeouts_are_retry(self):
        def slow(request):
            raise httpx.ReadTimeout("slow")
        self.assertEqual(self.fcm(slow).send(ANDROID, {"title": "t"}, "n").outcome, "retry")

    def test_malformed_private_key_is_permanent(self):
        bad = {**fcm_secret(), "private_key": "-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n"}
        r = self.fcm(lambda req: httpx.Response(200), secret=bad).send(ANDROID, {"title": "t"}, "n")
        self.assertEqual(r.outcome, "failed")
        self.assertIn("service account", r.reason.lower())

    def test_token_reused_until_near_expiry(self):
        calls = []

        def oauth(request):
            calls.append(1)
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        s = self.fcm(lambda r: httpx.Response(200), oauth=oauth)
        s.send(ANDROID, {"title": "t"}, "n")
        s._expiry = time.time() + 200  # inside the 5-minute refresh window
        s.send(ANDROID, {"title": "t"}, "n")
        self.assertEqual(len(calls), 2)

    def test_data_only_and_normal_priority_android(self):
        m = FcmSender.build(ANDROID, {"data": {"a": "1"}, "priority": "normal", "ttl_seconds": 0}, "n")["message"]
        self.assertNotIn("notification", m)
        self.assertEqual((m["android"]["priority"], m["android"]["ttl"]), ("NORMAL", "0s"))
        self.assertNotIn("notification", m["android"])

    def test_message_without_optional_fields(self):
        m = FcmSender.build(ANDROID, {"title": "only title"}, "n")["message"]
        self.assertEqual(m["notification"], {"title": "only title"})
        self.assertEqual(m["android"]["ttl"], "86400s")

    def test_parse_retry_after(self):
        self.assertEqual(parse_retry_after("30"), 30.0)
        self.assertIsNone(parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT"))
        self.assertIsNone(parse_retry_after(None))
        self.assertEqual(parse_retry_after("-5"), 0.0)


class ApnsEdgeTests(SimpleTestCase):
    def apns(self, handler, secret=None):
        return ApnsSender(secret or apns_secret(), client=client(handler))

    def test_production_host(self):
        seen = []
        self.apns(lambda r: seen.append(str(r.url)) or httpx.Response(200)).send(IOS, {"title": "t"}, "n")
        self.assertEqual(seen, ["https://api.push.apple.com/3/device/abc123"])

    def test_non_json_5xx_is_retry(self):
        self.assertEqual(self.apns(lambda r: httpx.Response(500, text="oops")).send(
            IOS, {"title": "t"}, "n").outcome, "retry")

    def test_bad_p8_is_permanent(self):
        bad = {**apns_secret(), "p8": "-----BEGIN PRIVATE KEY-----\ngarbage\n-----END PRIVATE KEY-----"}
        self.assertEqual(self.apns(lambda r: httpx.Response(200), bad).send(IOS, {"title": "t"}, "n").outcome,
                         "failed")

    def test_too_many_token_updates_is_retry(self):
        r = self.apns(lambda req: httpx.Response(429, json={"reason": "TooManyProviderTokenUpdates"})).send(
            IOS, {"title": "t"}, "n")
        self.assertEqual(r.outcome, "retry")

    def test_provider_token_reused_across_sends(self):
        auths = []
        s = self.apns(lambda r: auths.append(r.headers["authorization"]) or httpx.Response(200))
        for _ in range(5):
            s.send(IOS, {"title": "t"}, "n")
        self.assertEqual(len(set(auths)), 1)

    def test_forced_refresh_respects_20_minute_floor(self):
        s = self.apns(lambda r: httpx.Response(200))
        first = s._provider_token()
        self.assertEqual(s._provider_token(force=True), first)  # minted seconds ago: reuse
        s._issued_at -= 21 * 60
        self.assertNotEqual(s._provider_token(force=True), first)

    def test_byte_limit_with_multibyte_text(self):
        body = "ఆ" * 1300  # ~3,900 bytes UTF-8
        r = self.apns(lambda req: httpx.Response(200)).send(IOS, {"title": "t", "body": body + "ఆ" * 100}, "n")
        self.assertEqual(r.outcome, "failed")
        sent = []
        ok = self.apns(lambda req: sent.append(len(req.content)) or httpx.Response(200)).send(
            IOS, {"title": "t", "body": "ఆ" * 1000}, "n")
        self.assertEqual(ok.outcome, "sent")
        self.assertLessEqual(sent[0], 4096)

    def test_ttl_zero_means_now_or_never(self):
        headers, _ = ApnsSender.build(IOS, {"title": "t", "ttl_seconds": 0}, "n")
        self.assertEqual(headers["apns-expiration"], "0")

    def test_data_keys_and_badge_only_push(self):
        headers, payload = ApnsSender.build(IOS, {"badge": 0, "data": {"route": "/x"}}, "n")
        self.assertEqual(headers["apns-push-type"], "background")
        self.assertEqual(payload["aps"], {"badge": 0})
        self.assertEqual(payload["route"], "/x")
        json.dumps(payload)
