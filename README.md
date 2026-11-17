# Push Gateway

> **New here?** [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) walks through running, deploying, creating every key and onboarding an app. The same operator steps are inside the dashboard under **Help** (`/admin/help/`).

One self-hosted push notification service for all of your Android and iOS apps
(SC Boutique, CRM-B, and whatever comes next). Your servers call **one API**; the
gateway keeps track of every device, works out who should receive a message,
queues it, delivers it, retries it and cleans up dead devices.

* **Android** delivers through **FCM HTTP v1**, used only as the pipe. Android
  wakes a closed app only through Google's push channel, so there is no reliable
  alternative for Play Store apps. FCM is free and has no message limit.
* **iOS** delivers **directly to APNs** over HTTP/2 with your `.p8` key (free with
  your Apple Developer account). One key covers every app in the team.
* Users, targeting, scheduling, retries, history and the dashboard all live
  here, not in the Firebase console.

Same stack and layout as CRM-B: Django + DRF, feature apps under `src/<name>_app/`
with `models / controller / route / schemas / services / tests`, shared rules in
`src/core/`, background work as management commands.

```
gateway/                 settings, urls, wsgi
src/core/                auth (API keys), permissions, crypto, errors, throttles, test fixtures
src/tenants_app/         tenants, API keys, provider credentials, apps; /admin-api/ + onboarding commands
src/devices_app/         device registration, users, topics            → /v1/devices, /v1/users
src/notifications_app/   send, status, cancel, open tracking           → /v1/notifications, /v1/events
src/dispatch_app/        Postgres job queue, dispatcher, FCM + APNs providers
clients/flutter/push_gateway_client/   the Dart package every Flutter app uses
bin/start.sh             production entry (gunicorn + dispatcher)
render.yaml              Render Blueprint
docs/architecture.md     how it works, scaling, costs, decisions
```

## How a message travels

```
CRM-B ──POST /v1/notifications (sk_ key)──▶ API ──▶ Notification row + "expand" job   (202 in ms)
                                                     │
                                        dispatcher ◀─┘  pages the audience, 500 devices per batch
                                             │
                     ┌───────────────────────┼────────────────────────┐
                     ▼                                                ▼
              FCM HTTP v1 (Android)                            APNs HTTP/2 (iOS)
                     │                                                │
          404/UNREGISTERED → device deactivated          410/BadDeviceToken → device deactivated
          429/5xx → retried with backoff                 429/5xx → retried with backoff
```

## Keys

Each tenant (one of your products) gets three secrets when it is created:

| Secret | Where it lives | What it can do |
|---|---|---|
| `sk_…` secret key | your **server** only (CRM-B env var) | send, read status, list/delete a user's devices |
| `pk_…` publishable key | **inside the app** | register/unregister its own device, topics, report opens |
| identity secret | your **server** only | signs `user_token`s that prove a device belongs to a user |

The identity secret stops one user from attaching their phone to another user's
id and reading their notifications. After sign-in, your backend gives the app
`user_token = HMAC_SHA256(identity_secret, user_id)` (hex), and the app sends it
with the device registration.

## Run locally

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
createdb push_gateway
bash bin/dev.sh                     # migrates, starts the dispatcher and the API on :8000
DEBUG=True .venv/bin/python manage.py test
```

## Onboard an app (once per product)

1. **Firebase (Android):** Firebase console → your project → Project settings →
   Service accounts → *Generate new private key*. Add your Android app to the same
   project and put `google-services.json` in the Flutter app. One Firebase project
   can hold all your Android apps.
2. **Apple (iOS):** developer.apple.com → Keys → **+** → enable *Apple Push
   Notifications service (APNs)* → download `AuthKey_XXXX.p8`, note the Key ID and
   your Team ID. Enable the *Push Notifications* capability in Xcode.
3. Register them with the gateway, from the Render shell or locally:

```bash
python manage.py add_credential firebase-main --fcm-json firebase-service-account.json
python manage.py add_credential apple-main --apns-p8 AuthKey_ABC123.p8 --team-id TEAMID1234 --key-id ABC123
python manage.py create_tenant "SC Boutique"          # prints sk_, pk_ and identity secret ONCE
python manage.py add_app "SC Boutique" android com.sc.boutique firebase-main --channel orders
python manage.py add_app "SC Boutique" ios com.sc.boutique apple-main
```

The same is possible in the dashboard at `/admin/` (sign in with
`ADMIN_USERNAME`/`ADMIN_PASSWORD`), or over HTTP at `/admin-api/` with
`Authorization: Bearer $ADMIN_API_TOKEN`.

## API

All responses are JSON. Errors are always `{"error": "...", "code": "...", "details"?: {...}}`.

### Devices (from the app, `X-API-Key: pk_…`)

```http
POST /v1/devices
{
  "installation_id": "9f2c…",            // random, generated once per install, kept in storage
  "platform": "android",                 // or "ios"
  "bundle_id": "com.sc.boutique",
  "token": "<FCM token or APNs device token>",
  "apns_environment": "production",      // "sandbox" for iOS debug builds
  "user_external_id": "cust-42",          // optional
  "user_token": "<hmac from your backend>",
  "locale": "en-IN", "timezone": "Asia/Kolkata", "app_version": "1.0.0",
  "topics": ["offers", "hyderabad"]       // optional, replaces the device's topics
}
POST   /v1/devices/{installation_id}/logout?bundle_id=com.sc.boutique
DELETE /v1/devices/{installation_id}?bundle_id=com.sc.boutique
POST   /v1/devices/{installation_id}/topics   {"bundle_id": "...", "add": ["offers"], "remove": []}
POST   /v1/events   {"notification_id": "<_ngw_id from the push data>", "type": "opened"}
```

Call `POST /v1/devices` on every app start and after sign-in. It is an upsert.

### Sending (from your server, `X-API-Key: sk_…`)

```http
POST /v1/notifications
Idempotency-Key: order-1042-ready          (optional; retries return the original)
{
  "target":  {"user_ids": ["cust-42"]},     // or {"topics": [...]}, {"installation_ids": [...]}, {"all": true}
                                           // optional filters: "platforms": ["ios"], "bundle_ids": [...]
  "message": {
    "title": "Your order is ready",
    "body": "Pick it up any time after 4 pm",
    "data": {"route": "/orders/1042"},      // string values; delivered to the app
    "image": "https://…/blouse.jpg",
    "android_channel_id": "orders",
    "badge": 1, "sound": "default",
    "priority": "high",                     // "normal" for non-urgent
    "ttl_seconds": 86400,
    "collapse_key": "order-1042"            // newer replaces older on the device
  },
  "send_at": "2026-11-18T04:30:00Z"         // optional, schedules it
}
→ 202 {"id": "…", "status": "queued", …}

GET  /v1/notifications                 recent, with counters
GET  /v1/notifications/{id}            targeted / sent / failed / invalid_token / opened + recent failures
POST /v1/notifications/{id}/cancel
GET  /v1/users/{user_id}/devices
DELETE /v1/users/{user_id}             data deletion
```

### From CRM-B (Django)

```python
import hashlib, hmac, os, requests

PUSH_URL = os.environ["PUSH_GATEWAY_URL"]          # https://push-gateway.onrender.com
PUSH_KEY = os.environ["PUSH_GATEWAY_SECRET_KEY"]    # sk_…
PUSH_IDENTITY = os.environ["PUSH_GATEWAY_IDENTITY_SECRET"]

def push_user_token(user_id: str) -> str:
    """Return this with the login response so the app can link its device."""
    return hmac.new(PUSH_IDENTITY.encode(), user_id.encode(), hashlib.sha256).hexdigest()

def notify(user_ids, title, body, data=None, key=None):
    requests.post(f"{PUSH_URL}/v1/notifications", timeout=10,
                  headers={"X-API-Key": PUSH_KEY, **({"Idempotency-Key": key} if key else {})},
                  json={"target": {"user_ids": user_ids},
                        "message": {"title": title, "body": body, "data": data or {}}})
```

Use a user id that is unique within the tenant. For a multi-tenant product like CRM-B,
prefix it with the boutique: `"sarala:staff-12"`.

### Flutter apps

See [clients/flutter/push_gateway_client](clients/flutter/push_gateway_client/README.md).

## Deploy on Render

The database is **Supabase** (project `mobile_push`, Singapore); Render runs only the web service.

1. Render → **New → Web Service** → pick this repo, region **Singapore**, instance **Starter**:
   - Build: `pip install -r requirements.txt && python manage.py collectstatic --noinput`
   - Start: `bash bin/start.sh` (runs migrations first, then the API and dispatcher) · Health check path: `/healthz`
   - Env: `PYTHON_VERSION=3.12.8`, `RUN_DISPATCHER_INLINE=true`, `WEB_CONCURRENCY=2`,
     `DJANGO_SECRET_KEY` and `ADMIN_API_TOKEN` (Render's *Generate*), plus the ones below.

   (`render.yaml` describes the same service if you prefer **New → Blueprint**.)
2. Fill the secret env vars:
   - `DATABASE_URL`: Supabase → **Connect** → **Transaction pooler** string (port **6543**), password
     filled in, ending in `?sslmode=require`. (The direct `db.<ref>.supabase.co` host is IPv6-only; Render
     can't reach it.) On port 6543 settings turn off server-side cursors automatically.
   - `MASTER_ENCRYPTION_KEY`: `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`.
     **Store a copy in your password manager**; without it stored credentials can't be decrypted.
   - `ADMIN_USERNAME`, `ADMIN_PASSWORD` (optional): creates that admin on deploy **only if it doesn't
     exist**, so a password changed in `/admin/` survives deploys. `ADMIN_RESET_PASSWORD=true` for one
     deploy forces the env password.
3. Every start runs `migrate`, which also locks the gateway's tables away from Supabase's public
   Data API (`src/core/supabase.py`: revokes `anon`/`authenticated`, enables RLS).
4. After the first deploy, open the service **Shell** and run the onboarding commands above. Run them
   there, not on your laptop, so credentials are encrypted with the production `MASTER_ENCRYPTION_KEY`.
5. Check `https://<service>.onrender.com/healthz` and sign in at `/admin/`.

Supabase notes: the transaction pooler shares a small pool of real Postgres connections between
many clients, so adding dispatcher instances later needs no database change. Free
Supabase projects pause after a week without activity; the dispatcher queries every second, which
counts as activity, but Supabase Pro ($25/month) removes the risk and adds daily backups.

## Scaling

| Volume | Setup | ~USD/month |
|---|---|---|
| Start, up to ~20k devices | 1 Starter web (API + inline dispatcher) + Postgres basic-256mb | ~14 |
| ~100k devices, ~1M pushes/day | add `push-dispatcher` worker (Standard), `RUN_DISPATCHER_INLINE=false`, Postgres basic-1gb | ~60 |
| 1M+ devices, big broadcasts | 2 Standard web, 2–4 Standard dispatchers, Postgres basic-4gb | ~230–260 |

One dispatcher with 64 send threads does roughly 500–1,500 pushes/second
(bounded by provider latency). Add instances to go faster. They share the
queue through `SKIP LOCKED` and need no coordination. Prices are Render's list
prices at the time of writing; check render.com/pricing.

See [docs/architecture.md](docs/architecture.md) for the design decisions and failure handling.
