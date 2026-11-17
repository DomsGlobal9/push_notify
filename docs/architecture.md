# Architecture and decisions

## Pieces

| Piece | Runs as | Job |
|---|---|---|
| API | gunicorn (`gateway.wsgi`) | authenticate, validate, write rows, enqueue. Never talks to Google or Apple. |
| Dispatcher | `manage.py run_dispatcher` | claim jobs, page audiences, send over HTTP/2, record outcomes |
| Postgres | Render managed | all state **and** the job queue |
| Admin | Django admin at `/admin/` | onboarding, devices, notification history, queue, "send test" |

## Data

```
Tenant ──< ApiKey            (sk_/pk_, sha256-hashed)
   │
   ├──< App >── Credential   (FCM service account / APNs .p8, AES-256-GCM encrypted, shared across apps)
   │     │
   │     └──< Device ──< DeviceTopic
   │
   └──< Notification ──< DeliveryFailure   (failures only; successes are counters)

Job  (dispatch queue: expand | send_batch)
```

Tenancy is row-level. Every tenant-scoped query filters on the caller's tenant,
which comes from the API key. Tenants are your own products, so there are only
a few. Schema-per-tenant (CRM-B's model) would cost a query per tenant on every
broadcast and buy nothing the key scoping does not already give.

## Why these choices

**FCM for Android, APNs direct for iOS.** On Android, only Google's
OS-level channel wakes a closed app. The other options are a foreground
service holding a socket (a permanent notification, battery drain, Play
policy review) or UnifiedPush (every user installs a separate distributor app),
and neither works for normal customers. iOS has no such restriction, so it goes
straight to Apple and Firebase never sees iOS traffic. An iOS app can still be
registered with an FCM credential if you prefer FCM for both platforms.

**Postgres as the queue (no Redis to start).** Enqueueing runs in the same
transaction as the data it belongs to. A notification and its first job commit
together. A batch's counters, its dead-token cleanup, its retry batch and its
own removal also commit together. Redis would need an outbox table to match
that, and costs another $10+/month. `queue.py` is the only module that knows
the queue is Postgres, so switching later touches one file.

**Lazy audience expansion.** The audience is read 500 device ids at a time
(keyset on `id`), and each page becomes one `send_batch` job. A million-device
broadcast is a chain of small jobs. Memory use does not grow with the
audience, and other tenants' sends interleave with it instead of waiting.

**Threads for HTTP, one transaction for the database.** A batch is sent from
a thread pool over long-lived HTTP/2 clients (one per credential, which keeps
OAuth and JWT tokens warm). Only the main thread touches the database.

**Database time for scheduling.** `run_at` is written and compared with
Postgres `clock_timestamp()`, so clock skew between hosts cannot fire jobs
early or hold them back.

## Failure handling

| What happens | What the gateway does |
|---|---|
| FCM 404 `UNREGISTERED`, 403 `SENDER_ID_MISMATCH`, 400 bad token | device deactivated, failure logged, counted in `invalid_token_count` |
| APNs 410, `BadDeviceToken`, `DeviceTokenNotForTopic` | same |
| 429 / 5xx / network error | device put in a retry batch with delay `max(Retry-After, 5·2^attempt s)`, up to 5 attempts, then counted failed |
| FCM 401 | refresh the OAuth token once and resend |
| APNs `ExpiredProviderToken` | mint a new JWT (never more than once per 20 min) and resend |
| Bad payload or credential | failed for that device, reason logged; other devices continue |
| Provider code throws | that device is retried; the batch continues |
| Dispatcher dies mid-job | job returns to the queue after 10 minutes (`recover_stale`). Delivery is at-least-once, so a crash can resend one batch. Use `collapse_key` where a duplicate would matter. |
| Same send request retried | `Idempotency-Key` returns the original notification |
| Token reused by a new install | old row retired (`token_moved`), so there are no duplicate pushes |

## Retention

The dispatcher purges hourly:
- delivery failures after 14 days
- finished notifications after 90 days
- dead jobs after 7 days

Tune with `PUSH_*_RETENTION_DAYS`. Devices are never purged automatically.
Inactive rows stay so the admin can see why a device stopped.

## Security

- Provider secrets and identity secrets are AES-256-GCM encrypted under
  `MASTER_ENCRYPTION_KEY` and versioned (`v1:`) for rotation. **Back up that key.**
- API keys are stored as SHA-256 hashes and shown once.
- `pk_` keys can't send.
- Linking a user needs an HMAC signed by the tenant's backend.
- Per-tenant throttles: `THROTTLE_SEND`, `THROTTLE_DEVICE`. They are
  per-process; switch `CACHES` to Redis for a global cap when you run several
  web instances.
- `/admin-api/` is disabled unless `ADMIN_API_TOKEN` is set.

## Later (not built yet)

- Quiet hours and sending in each user's timezone (the device row already stores the timezone).
- Templates and localisation (the device row already stores the locale).
- Web Push (VAPID) and Huawei Push Kit as more providers in `src/dispatch_app/providers/`.
- WebSocket/SSE for in-app real-time updates.
