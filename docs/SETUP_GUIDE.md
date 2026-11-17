# Developer setup guide

Everything a developer needs: run the gateway, deploy it, create every key, onboard a new app, and wire
an app and a backend to it. The same operator steps are also inside the dashboard under **Help**
(`/admin/help/`), with links straight to the right pages.

> Only the `pk_` key and the Firebase config files ever go inside an app. Every other key in this guide
> stays on a server, in the dashboard (encrypted) or in a password manager. Never commit one to git.

---

## 1. Run the gateway locally

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.txt
createdb push_gateway
bash bin/dev.sh            # migrates, starts the dispatcher and the API on :8000
```

- Dashboard: <http://localhost:8000/admin/>. Create a login with
  `DEBUG=True .venv/bin/python manage.py createsuperuser`.
- Tests: `DEBUG=True .venv/bin/python manage.py test` (182+ tests, about 20 s, local Postgres).
- CI runs the same tests on every push (`.github/workflows/ci.yml`).

## 2. Deploy (Render web service + Supabase)

| Render field | Value |
|---|---|
| Runtime | Python 3, region Singapore, branch `main` |
| Build | `pip install -r requirements.txt && python manage.py collectstatic --noinput` |
| Start | `bash bin/start.sh` (migrates, then runs the API and the dispatcher) |
| Health check | `/healthz` |

Environment variables:

| Variable | How to get it |
|---|---|
| `DATABASE_URL` | Supabase → Connect → **Transaction pooler** (port 6543), password filled in, `?sslmode=require` |
| `MASTER_ENCRYPTION_KEY` | `python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`. **Back it up**: losing or changing it makes stored credentials unreadable. |
| `DJANGO_SECRET_KEY`, `ADMIN_API_TOKEN` | Render's **Generate** button |
| `PYTHON_VERSION=3.12.8`, `RUN_DISPATCHER_INLINE=true`, `WEB_CONCURRENCY=2` | fixed |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` (optional) | Creates that admin on start **only if it does not exist**. Add `ADMIN_RESET_PASSWORD=true` for one deploy to force-reset. |

On the free Render plan, point an uptime monitor (e.g. UptimeRobot, HTTP(s), every 5 min) at
`https://<service>.onrender.com/healthz` so the service never sleeps.

Every start runs `migrate`, which also locks all gateway tables away from Supabase's public Data API
(`src/core/supabase.py`).

## 3. Create the provider keys (once, for all apps)

### Firebase service account (Android delivery)

1. <https://console.firebase.google.com> → your project (one project holds all Android apps).
2. ⚙️ Project settings → **Service accounts** → **Generate new private key** → **Generate key**. A `.json`
   file downloads. The language picker on that page does not matter.
3. **Cloud Messaging** tab: *Firebase Cloud Messaging API (V1)* must be **Enabled**.
4. Dashboard → **Credentials → Add** → provider *Firebase Cloud Messaging* → paste the whole JSON → Save.
5. Keep the JSON in a password manager and delete the download.

### Apple `.p8` key (iOS delivery)

1. <https://developer.apple.com> → Certificates, Identifiers & Profiles → **Keys** → **+**.
2. Tick **Apple Push Notifications service (APNs)** (Sandbox & Production) → Continue → Register.
3. **Download** `AuthKey_XXXXXXXXXX.p8`. Apple allows **one** download only.
4. Note the **Key ID** (10 characters) and the **Team ID** (top right / Membership).
5. Dashboard → **Credentials → Add** → provider *Apple Push Notification service* → Team ID, Key ID,
   the full `.p8` text including the BEGIN/END lines → Save.

Scripted alternative (needs the production `MASTER_ENCRYPTION_KEY`, so run it in the Render shell, not on a laptop):

```bash
python manage.py add_credential firebase-main --fcm-json service-account.json
python manage.py add_credential apple-main --apns-p8 AuthKey_ABC123.p8 --team-id TEAMID1234 --key-id ABC123
```

Or over HTTPS with `Authorization: Bearer $ADMIN_API_TOKEN`: `POST /admin-api/credentials`
(the server encrypts with its own key).

## 4. Onboard a new app

1. **Firebase**: Project settings → General → **Add app** → Android → exact package name (`applicationId`)
   → download `google-services.json`. For iOS: Add app → iOS → download `GoogleService-Info.plist`.
2. **Apple**: Identifiers → the bundle id → tick **Push Notifications**.
3. **Dashboard → Products → Add**: the product name → Save. Copy the three values shown **once**:

   | Value | Goes to |
   |---|---|
   | `sk_…` secret key | backend env `PUSH_GATEWAY_SECRET_KEY` |
   | `pk_…` publishable key | the app's env (e.g. `PUSH_GATEWAY_KEY` in `env/prod.json`) |
   | identity secret | backend env `PUSH_GATEWAY_IDENTITY_SECRET` (product page → *Show identity secret* shows it again) |

   Lost the keys: product page → **Rotate keys** (old ones stop working immediately).
4. **Dashboard → Apps → Add**, once per platform: product, platform, bundle id, credential
   (`firebase-main` for Android, `apple-main` for iOS), Android channel id (e.g. `orders`).
   Build flavors with different package names (`.dev`, `.staging`) need their own app rows.

## 5. Wire the Flutter app

See `clients/flutter/push_gateway_client/README.md`. In short:

```yaml
dependencies:
  firebase_core: ^4.15.2
  firebase_messaging: ^16.7.1
  push_gateway_client:
    git:
      url: https://github.com/DomsGlobal9/push_notify.git
      path: clients/flutter/push_gateway_client
```

```dart
await Firebase.initializeApp();
final push = PushGateway(const PushGatewayConfig(
  baseUrl: 'https://push-notify-7upj.onrender.com',
  publishableKey: 'pk_...',
  androidPackage: 'com.example.app',
  iosBundleId: 'com.example.app',
));
await push.start();                                   // each launch, after login
await push.signIn(userId: id, userToken: token);      // when the backend sends them
push.opened.listen((m) => router.go(m.data['route'] ?? '/'));
await push.signOut();                                 // on logout
```

Android also needs: the `com.google.gms.google-services` Gradle plugin, the notification channel created at
start, a single-colour status-bar icon (`default_notification_icon` meta-data). SC Boutique is the reference
implementation (`lib/features/push/`, `android/app/build.gradle.kts`, `MainActivity.kt`).

## 6. Wire the backend

```python
import hashlib, hmac, os, requests

def push_identity(tenant_id, user_id):            # add to login and /me responses
    uid = f"{tenant_id}:{user_id}"
    token = hmac.new(os.environ["PUSH_GATEWAY_IDENTITY_SECRET"].encode(),
                     uid.encode(), hashlib.sha256).hexdigest()
    return {"push_user_id": uid, "push_user_token": token}

def notify(user_ids, title, body, route=None, key=None):
    requests.post(f"{os.environ['PUSH_GATEWAY_URL']}/v1/notifications", timeout=10,
                  headers={"X-API-Key": os.environ["PUSH_GATEWAY_SECRET_KEY"],
                           **({"Idempotency-Key": key} if key else {})},
                  json={"target": {"user_ids": user_ids},
                        "message": {"title": title, "body": body,
                                    "data": {"route": route} if route else {}}})
```

Full API (targets, message fields, error codes): `README.md` and dashboard **Help → Sending from your backend**.

## 7. Test end to end

1. Real phone, release-like build, sign in, allow notifications.
2. Dashboard → **Devices**: the phone shows **Active**.
3. App in the background → **Notifications → Send test notification** → product → Send.
4. Phone buzzes; tap opens the app; the notification shows *Sent 1/1*, *Opened 1*.
5. Problems: dashboard **Help → Troubleshooting** (every failure reason and its fix).

## 8. Before a store release

- [ ] Firebase files present in the release build (SC Boutique's Gradle fails a prod release without one).
- [ ] Push switched on in the release environment file, with the `pk_` key (never `sk_`).
- [ ] Version code higher than the last upload.
- [ ] Play Console → Data safety: *Device or other IDs: collected, app functionality*.
- [ ] iOS: Push Notifications capability in the release build, APNs credential added.
- [ ] Backend sends real events and links users at login.
- [ ] Uptime monitor on `/healthz`.

## 9. Rotating secrets

| Secret | How |
|---|---|
| `sk_` / `pk_` | Product → **Rotate keys**, update backend env, ship the app with the new `pk_` |
| Identity secret | Ask in code review; rotation means re-login for linked users |
| Firebase key | Firebase → Service accounts → delete old key, generate new, paste into the credential |
| Apple `.p8` | Revoke in Apple portal, create new, paste into the credential |
| Database password | Supabase → reset, update `DATABASE_URL` in Render |
| `MASTER_ENCRYPTION_KEY` | Not rotatable in place yet: keep the original backed up |
