"""A real two-connection race, isolated in a disposable PostgreSQL database."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import threading
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest
from fastapi import HTTPException
from app.config import dsn
from app.db import SYSTEM, one
from app import geo, passages
from app.main import GeoInput
import test_integration

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE") != "1", reason="Real PostgreSQL is required"
    ),
]


def test_coordinate_request_waiting_on_exit_cannot_append_after_closure(monkeypatch):
    database = "prohod_race_test_" + uuid4().hex
    owner = conninfo_to_dict(dsn("owner"))
    owner["dbname"] = "postgres"
    runtime = conninfo_to_dict(dsn())
    runtime["dbname"] = database
    with psycopg.connect(**owner, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            with psycopg.connect(**dict(owner, dbname=database)) as setup:
                setup.execute(
                    (Path(__file__).parent.parent / "sql/001_schema.sql").read_text(
                        encoding="utf-8"
                    )
                )
                setup.execute(
                    "INSERT INTO principals(id,issuer,subject,username,roles) VALUES(%s,'test','system','system',ARRAY['admin'])",
                    (SYSTEM.id,),
                )

            @contextmanager
            def isolated_transaction(actor, **kwargs):
                with psycopg.connect(**runtime, row_factory=dict_row) as conn:
                    conn.execute(
                        "SELECT set_config('app.actor',%s,true),set_config('app.roles',%s,true)",
                        (str(actor.id), "".join("|" + r + "|" for r in actor.roles)),
                    )
                    yield conn

            monkeypatch.setattr(test_integration, "transaction", isolated_transaction)
            fixture = test_integration.fixture_db.__wrapped__(monkeypatch)
            conn, ids, actors, context = next(fixture)
            try:
                context(actors["guard"])
                attempt = passages.start_attempt(
                    conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
                )
                conn.execute(
                    "INSERT INTO device_confirmations(attempt_id,employee_id,device_key_id,signature) VALUES(%s,%s,%s,%s)",
                    (attempt["id"], ids["employee"], ids["key"], "test"),
                )
                shift = passages.finalize(
                    conn, actors["guard"], attempt["id"], {"passed": True}
                )["shift_id"]
                conn.commit()
                ready = threading.Event()
                sample = GeoInput(
                    shift_id=shift,
                    sample_id=uuid4(),
                    measured_at=datetime.now(timezone.utc),
                    latitude=55.5,
                    longitude=37.5,
                    accuracy_m=5,
                )

                def send():
                    with isolated_transaction(actors["worker"]) as location_conn:
                        ready.set()
                        try:
                            geo.record_location(location_conn, actors["worker"], sample)
                        except HTTPException as exc:
                            return exc.status_code
                        return 200

                with ThreadPoolExecutor(max_workers=1) as pool:
                    with isolated_transaction(actors["guard"]) as closing:
                        closing.execute(
                            "UPDATE shifts SET closed_at=now() WHERE id=%s", (shift,)
                        )
                        future = pool.submit(send)
                        assert ready.wait(2)
                        # Inspect the database wait instead of assuming scheduler timing.
                        waiting = False
                        for _ in range(100):
                            waiting = admin.execute(
                                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=%s AND wait_event_type=%s)",
                                (database, "Lock"),
                            ).fetchone()[0]
                            if waiting:
                                break
                            threading.Event().wait(0.01)
                        assert waiting, "GPS request must wait on the locked shift"
                    assert future.result(timeout=5) == 409
                context(SYSTEM)
                assert (
                    one(
                        conn,
                        "SELECT count(*) AS n FROM location_checks WHERE shift_id=%s",
                        (shift,),
                    )["n"]
                    == 0
                )
            finally:
                fixture.close()
        finally:
            assert database.startswith("prohod_race_test_")
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )
