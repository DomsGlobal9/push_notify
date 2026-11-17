from unittest import mock

from django.db import connection
from django.test import TestCase

from src.core import supabase


class LockdownTests(TestCase):
    def test_noop_without_supabase_roles(self):
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
            if cur.fetchone()[0]:
                self.skipTest("this Postgres has Supabase roles")
        self.assertEqual(supabase.lock_down(), 0)

    def test_issues_revoke_and_rls_for_every_table_when_roles_exist(self):
        executed = []
        real_cursor = connection.cursor

        class Spy:
            def __init__(self):
                self.cur = real_cursor()

            def __enter__(self):
                self.inner = self.cur.__enter__()
                return self

            def __exit__(self, *a):
                return self.cur.__exit__(*a)

            def execute(self, sql, params=None):
                executed.append(sql)
                if sql.startswith("SELECT rolname"):
                    self._rows = [("anon",), ("authenticated",)]
                    return None
                if sql.startswith(("REVOKE", "ALTER")):
                    return None  # roles don't exist locally; just record
                return self.inner.execute(sql, params)

            def fetchall(self):
                rows = self.__dict__.pop("_rows", None)
                return rows if rows is not None else self.inner.fetchall()

            def __getattr__(self, name):
                return getattr(self.inner, name)

        with mock.patch.object(connection, "cursor", Spy):
            count = supabase.lock_down()
        self.assertGreaterEqual(count, 15)
        rls = [s for s in executed if "ENABLE ROW LEVEL SECURITY" in s]
        self.assertEqual(len(rls), count)
        self.assertTrue(any('"tenants_app_credential"' in s for s in rls))
        self.assertTrue(any('"django_migrations"' in s for s in rls))
        self.assertTrue(any("DEFAULT PRIVILEGES" in s for s in executed))
