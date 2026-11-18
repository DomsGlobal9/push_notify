# CRM-B: follow-ups for the push integration

Found while auditing the full path (CRM-B → gateway → FCM → phone). The integration in commit `6c6020a`
is correct and verified live: alerts created in the web reach the gateway with the right
`"<boutique>:<user id>"` addresses. These are the remaining items, in order of importance.

## 1. Scheduled jobs can lose their pushes (fix before relying on reminders)

`src/core/push.py` posts each push from a **daemon thread** (`_start`). The cron jobs
`send_delivery_reminders` and `send_birthday_wishes` are short-lived processes: they create the alert
rows and exit, which kills daemon threads, so the HTTP call to the gateway may never happen.

Fix (either):
- in those two commands set `settings.PUSH_IN_BACKGROUND = False` at the start of `handle()` so
  `_start` posts synchronously (the switch already exists), or
- make the thread non-daemon (`threading.Thread(..., daemon=False)`), so the process waits for it.

## 2. Render cron jobs are separate services

Each Cron Job has its own environment. Add `PUSH_GATEWAY_URL`, `PUSH_GATEWAY_SECRET_KEY` and
`PUSH_GATEWAY_IDENTITY_SECRET` to `send_delivery_reminders` and `send_birthday_wishes` (a Render
Environment Group attached to the web service and every cron job is the easy way). Make sure the
`send_delivery_reminders` job exists (`*/15 * * * *`, `python manage.py send_delivery_reminders`).

## 3. Let a tap open the right screen (optional)

`bell_push.push_bell` sends no `route`. The mobile app now opens **Notifications** for a push without
one. To open the order itself, pass `route=f"/orders/{order_ref}"` (any in-app path starting with `/`)
to `push.send(...)` where the order is known.

## 4. Lock-screen privacy (recommendation)

Alerts such as "A new custom order has been received for client <name>" show customer names on the
lock screen. If that is not wanted, shorten those bodies for push (keep the full text in the bell), or ask
for the gateway to mark pushes `visibility: private`.

## 5. Already correct, for the record

- Every alert goes through `Notification` + one `post_save` hook; no `bulk_create` bypass.
- Voice notes stay bell-only (`push.muted()`); customer rows are never pushed.
- `push_user_id` / `push_user_token` are returned by login and `/auth/me/`; idempotency keys are
  `bell-<schema>-<pk>`; deleted staff have their phones removed.
