"""Keep the gateway's tables out of Supabase's public Data API.

Supabase exposes every table in the `public` schema over its REST/GraphQL API to
the `anon` and `authenticated` roles, which anyone holding the project's
publishable key can use. The gateway never uses that API: it talks to Postgres
directly as the owner. So after every `migrate`, for every Django-managed table:

  * revoke all privileges from anon and authenticated (and for future tables),
  * enable Row Level Security with no policies, as a second lock.

The owner role Django connects as bypasses RLS, so the app is unaffected. On any
Postgres without those roles (local dev, CI, Render Postgres) this is a no-op.
"""

import logging

from django.db import connections

logger = logging.getLogger(__name__)

API_ROLES = ("anon", "authenticated")


def lock_down(using: str = "default") -> int:
    conn = connections[using]
    if conn.vendor != "postgresql":
        return 0
    with conn.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", [list(API_ROLES)])
        roles = [r[0] for r in cur.fetchall()]
        if not roles:
            return 0
        existing = set(conn.introspection.table_names(cur))
        tables = sorted(
            set(conn.introspection.django_table_names(only_existing=True, include_views=False))
            | ({"django_migrations"} & existing)  # not a model, but Django's and ours to protect
        )
        role_list = ", ".join(roles)
        for table in tables:
            cur.execute(f'REVOKE ALL ON TABLE public."{table}" FROM {role_list}')
            cur.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        cur.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role_list}")
        cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role_list}")
        cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role_list}")
    logger.info("Locked %s tables away from Supabase API roles %s", len(tables), roles)
    return len(tables)


def on_post_migrate(sender, using="default", **kwargs):
    # post_migrate fires once per app; run once, after the last of ours.
    if sender.label == "dispatch_app":
        lock_down(using)
