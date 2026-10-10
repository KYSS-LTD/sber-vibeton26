import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import json
import pytest
import psycopg
from psycopg.types.json import Jsonb
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.db import transaction, SYSTEM, Actor, one
from app import passages, geo
from app.main import app, GeoInput
from app.auth import authenticate
from app.config import dsn

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("TEST_DATABASE") != "1",
        reason="Set TEST_DATABASE=1 for real PostgreSQL tests",
    ),
]


@pytest.fixture
def fixture_db(monkeypatch):
    with transaction(SYSTEM) as conn:
        ids = {
            name: uuid4()
            for name in [
                "guard",
                "worker",
                "manager",
                "admin",
                "employee",
                "a",
                "b",
                "checkpoint",
                "reference",
                "key",
                "session",
                "qr",
            ]
        }
        ids["qr_token"] = "test-qr-token-" + uuid4().hex
        for name, role in [
            ("guard", "controller"),
            ("worker", "employee"),
            ("manager", "manager"),
            ("admin", "admin"),
        ]:
            conn.execute(
                "INSERT INTO principals(id,issuer,subject,username,roles) VALUES(%s,%s,%s,%s,%s)",
                (ids[name], "test", str(ids[name]), "test-" + str(ids[name]), [role]),
            )
        conn.execute(
            "INSERT INTO employees(id,principal_id,full_name,personnel_number) VALUES(%s,%s,%s,%s)",
            (ids["employee"], ids["worker"], "TEST сотрудник", str(ids["worker"])),
        )
        polygon = json.dumps(
            {
                "type": "Polygon",
                "coordinates": [[[37, 55], [38, 55], [38, 56], [37, 56], [37, 55]]],
            }
        )
        for name in ["a", "b"]:
            conn.execute(
                "INSERT INTO sites(id,name,boundary,active) VALUES(%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326),true)",
                (ids[name], "TEST " + name, polygon),
            )
        conn.execute(
            "INSERT INTO checkpoints(id,site_id,name) VALUES(%s,%s,%s)",
            (ids["checkpoint"], ids["a"], "TEST КПП"),
        )
        conn.execute(
            "INSERT INTO controller_assignments(principal_id,site_id) VALUES(%s,%s)",
            (ids["guard"], ids["a"]),
        )
        for name, kind in [("worker", "employee"), ("manager", "manager")]:
            conn.execute(
                "INSERT INTO site_memberships(principal_id,site_id,kind) VALUES(%s,%s,%s)",
                (ids[name], ids["a"], kind),
            )
        conn.execute(
            "INSERT INTO face_reference_sets(id,employee_id,version,state,model_version) VALUES(%s,%s,1,'draft','test')",
            (ids["reference"], ids["employee"]),
        )
        for slot in range(1, 4):
            media = one(
                conn,
                "INSERT INTO media_objects(employee_id,purpose,bucket,object_key,content_type,sha256,size_bytes) VALUES(%s,'reference','biometrics',%s,'image/jpeg',%s,1) RETURNING id",
                (ids["employee"], str(uuid4()), str(uuid4())),
            )["id"]
            photo = one(
                conn,
                "INSERT INTO face_reference_photos(set_id,slot,media_id,quality,image_sha256) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                (ids["reference"], slot, media, Jsonb({}), str(uuid4())),
            )["id"]
            conn.execute(
                "INSERT INTO face_templates(photo_id,embedding,model_version) VALUES(%s,%s,%s)",
                (photo, [1.0] + [0.0] * 127, "test"),
            )
        conn.execute(
            "UPDATE face_reference_sets SET state='active' WHERE id=%s",
            (ids["reference"],),
        )
        conn.execute(
            "INSERT INTO employee_device_keys(id,employee_id,public_jwk) VALUES(%s,%s,%s)",
            (ids["key"], ids["employee"], Jsonb({})),
        )
        conn.execute(
            "INSERT INTO gate_sessions(id,controller_id,checkpoint_id,site_id) VALUES(%s,%s,%s,%s)",
            (ids["session"], ids["guard"], ids["checkpoint"], ids["a"]),
        )
        from app.security import digest, encrypt

        conn.execute(
            "INSERT INTO daily_qr_credentials(id,employee_id,site_id,local_date,token_hash,token_encrypted,expires_at) VALUES(%s,%s,%s,current_date,%s,%s,now()+interval '1 day')",
            (
                ids["qr"],
                ids["employee"],
                ids["a"],
                digest(ids["qr_token"]),
                encrypt(ids["qr_token"]),
            ),
        )
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        actors = {
            name: Actor(ids[name], frozenset({role}))
            for name, role in [
                ("guard", "controller"),
                ("worker", "employee"),
                ("manager", "manager"),
                ("admin", "admin"),
            ]
        }

        def context(actor):
            conn.execute(
                "SELECT set_config('app.actor',%s,true),set_config('app.roles',%s,true)",
                (str(actor.id), "".join("|" + r + "|" for r in actor.roles)),
            )

        @contextmanager
        def shared_transaction(actor, **kwargs):
            with conn.transaction():
                context(actor)
                yield conn

        import app.main as main

        monkeypatch.setattr(main, "transaction", shared_transaction)
        try:
            yield conn, ids, actors, context
        finally:
            app.dependency_overrides.clear()
            conn.rollback()


def test_rls_and_api_block_other_site(fixture_db):
    conn, ids, actors, context = fixture_db
    context(actors["guard"])
    assert one(conn, "SELECT id FROM sites WHERE id=%s", (ids["b"],)) is None
    app.dependency_overrides[authenticate] = lambda: actors["guard"]
    client = TestClient(app)
    assert client.get(f"/api/v1/sites/{ids['b']}/checkpoints").status_code == 404
    assert client.get(f"/api/v1/sites/{ids['b']}/onsite").status_code in [403, 404]
    assert client.post(
        "/api/v1/assistant",
        json={
            "site_id": str(ids["b"]),
            "question": "SQL injection",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-02T00:00:00Z",
        },
    ).status_code in [403, 404]


def test_reusable_daily_qr_and_atomic_one_open_shift(fixture_db):
    conn, ids, actors, context = fixture_db
    context(actors["guard"])
    entry = passages.start_attempt(
        conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
    )
    conn.execute(
        "INSERT INTO device_confirmations(attempt_id,employee_id,device_key_id,signature) VALUES(%s,%s,%s,%s)",
        (entry["id"], ids["employee"], ids["key"], "test"),
    )
    result = passages.finalize(conn, actors["guard"], entry["id"], {"passed": True})
    assert result["state"] == "allowed"
    again = passages.finalize(conn, actors["guard"], entry["id"], {"passed": True})
    assert again["shift_id"] == result["shift_id"]
    assert (
        one(
            conn,
            "SELECT count(*) AS n FROM shifts WHERE employee_id=%s AND closed_at IS NULL",
            (ids["employee"],),
        )["n"]
        == 1
    )
    with pytest.raises(HTTPException):
        passages.start_attempt(
            conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
        )
    exit_attempt = passages.start_attempt(
        conn, actors["guard"], ids["session"], ids["qr_token"], "exit"
    )
    assert exit_attempt["id"] != entry["id"]
    conn.execute(
        "INSERT INTO device_confirmations(attempt_id,employee_id,device_key_id,signature) VALUES(%s,%s,%s,%s)",
        (exit_attempt["id"], ids["employee"], ids["key"], "test"),
    )
    exit_result = passages.finalize(
        conn, actors["guard"], exit_attempt["id"], {"passed": True}
    )
    assert exit_result["state"] == "allowed"
    context(actors["worker"])
    sample = GeoInput(
        shift_id=result["shift_id"],
        sample_id=uuid4(),
        measured_at=datetime.now(timezone.utc),
        latitude=55.5,
        longitude=37.5,
        accuracy_m=10,
    )
    with pytest.raises(HTTPException) as exc:
        geo.record_location(conn, actors["worker"], sample)
    assert exc.value.status_code == 409
    assert (
        one(
            conn,
            "SELECT count(*) AS n FROM location_checks WHERE shift_id=%s",
            (result["shift_id"],),
        )["n"]
        == 0
    )


def test_guard_reassignment_invalidates_pending_attempt(fixture_db):
    conn, ids, actors, context = fixture_db
    context(actors["guard"])
    entry = passages.start_attempt(
        conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
    )
    app.dependency_overrides[authenticate] = lambda: actors["admin"]
    response = TestClient(app).post(
        "/api/v1/assignments",
        json={
            "principal_id": str(ids["guard"]),
            "site_id": str(ids["b"]),
            "kind": "controller",
            "active": True,
        },
    )
    assert response.status_code == 200, response.text
    context(actors["admin"])
    assert (
        one(conn, "SELECT state FROM passage_attempts WHERE id=%s", (entry["id"],))[
            "state"
        ]
        == "cancelled"
    )
    context(actors["guard"])
    with pytest.raises(HTTPException):
        passages.finalize(conn, actors["guard"], entry["id"], {"passed": True})


def test_three_templates_constraint(fixture_db):
    conn, ids, actors, context = fixture_db
    context(actors["admin"])
    with pytest.raises(psycopg.Error):
        with conn.transaction():
            conn.execute(
                "UPDATE face_reference_sets SET state='retired' WHERE id=%s",
                (ids["reference"],),
            )
            row = one(
                conn,
                "INSERT INTO face_reference_sets(employee_id,version,state,model_version) VALUES(%s,2,'active','test') RETURNING id",
                (ids["employee"],),
            )
            conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_geolocation_only_during_shift_and_no_auto_close(fixture_db):
    conn, ids, actors, context = fixture_db
    context(actors["worker"])
    sample = GeoInput(
        shift_id=uuid4(),
        sample_id=uuid4(),
        measured_at=datetime.now(timezone.utc),
        latitude=55,
        longitude=37,
        accuracy_m=10,
    )
    with pytest.raises(HTTPException):
        geo.record_location(conn, actors["worker"], sample)
    context(actors["guard"])
    entry = passages.start_attempt(
        conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
    )
    conn.execute(
        "INSERT INTO device_confirmations(attempt_id,employee_id,device_key_id,signature) VALUES(%s,%s,%s,%s)",
        (entry["id"], ids["employee"], ids["key"], "test"),
    )
    result = passages.finalize(conn, actors["guard"], entry["id"], {"passed": True})
    context(actors["worker"])
    sample = GeoInput(
        shift_id=result["shift_id"],
        sample_id=uuid4(),
        measured_at=datetime.now(timezone.utc),
        latitude=54,
        longitude=36,
        accuracy_m=10,
    )
    point = geo.record_location(conn, actors["worker"], sample)
    assert not point["inside"]
    assert geo.record_location(conn, actors["worker"], sample)["id"] == point["id"]
    assert (
        one(conn, "SELECT closed_at FROM shifts WHERE id=%s", (result["shift_id"],))[
            "closed_at"
        ]
        is None
    )
    assert (
        one(
            conn,
            "SELECT count(*) AS n FROM alerts WHERE shift_id=%s",
            (result["shift_id"],),
        )["n"]
        == 1
    )


def test_analytics_role_is_read_only():
    with transaction(SYSTEM, analytics=True) as conn:
        assert one(
            conn,
            "SELECT current_user AS role,current_setting('transaction_read_only') AS readonly",
        ) == {"role": "prohod_analytics", "readonly": "on"}
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE sites SET name='forged'")
        conn.rollback()


@pytest.mark.parametrize("role", ["app", "analytics"])
def test_runtime_roles_cannot_bypass_rls_or_own_tables(role):
    with psycopg.connect(dsn(role)) as conn:
        flags = conn.execute(
            "SELECT rolsuper,rolbypassrls,pg_has_role(current_user,'prohod_owner','MEMBER') FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        assert flags == (False, False, False)
        assert not conn.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace='public'::regnamespace AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user))"
        ).fetchone()[0]
        if role == "analytics":
            assert conn.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"


def test_guard_manager_role_cannot_expand_checkpoint_authority(fixture_db):
    conn, ids, actors, context = fixture_db
    context(actors["admin"])
    conn.execute(
        "INSERT INTO site_memberships(principal_id,site_id,kind) VALUES(%s,%s,'manager')",
        (ids["guard"], ids["b"]),
    )
    combined = Actor(ids["guard"], frozenset({"controller", "manager"}))
    context(combined)
    from app.db import authorize_site, authorize_staff_site

    assert authorize_staff_site(conn, combined, ids["a"])["id"] == ids["a"]

    assert authorize_site(conn, combined, ids["b"], "manager")["id"] == ids["b"]
    with pytest.raises(HTTPException) as exc:
        authorize_site(conn, combined, ids["b"], "gate")
    assert exc.value.status_code == 403


def test_expired_revoked_qr_and_parallel_attempts(fixture_db):
    conn, ids, actors, context = fixture_db
    context(actors["guard"])
    entry = passages.start_attempt(
        conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
    )
    with pytest.raises(HTTPException) as exc:
        passages.start_attempt(
            conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
        )
    assert exc.value.status_code == 409
    conn.execute(
        "UPDATE daily_qr_credentials SET revoked_at=now() WHERE id=%s", (ids["qr"],)
    )
    result = passages.finalize(conn, actors["guard"], entry["id"], {"passed": True})
    assert result["state"] == "denied" and "QR" in result["reason"]
    assert (
        one(
            conn,
            "SELECT count(*) AS n FROM shifts WHERE employee_id=%s",
            (ids["employee"],),
        )["n"]
        == 0
    )
    conn.execute(
        "UPDATE daily_qr_credentials SET revoked_at=NULL,expires_at=now()-interval '1 second' WHERE id=%s",
        (ids["qr"],),
    )
    with pytest.raises(HTTPException):
        passages.start_attempt(
            conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
        )


def test_daily_qr_rotates_at_site_midnight(fixture_db, monkeypatch):
    conn, ids, actors, context = fixture_db
    context(actors["worker"])
    monkeypatch.setattr(
        passages,
        "utcnow",
        lambda: datetime(2030, 1, 1, 20, 59, 59, tzinfo=timezone.utc),
    )
    first = passages.daily_qr(conn, actors["worker"], ids["a"])
    assert first["expires_at"] == datetime(2030, 1, 1, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(
        passages, "utcnow", lambda: datetime(2030, 1, 1, 21, tzinfo=timezone.utc)
    )
    second = passages.daily_qr(conn, actors["worker"], ids["a"])
    assert first["token"] != second["token"]
    assert second["expires_at"] == datetime(2030, 1, 2, 21, tzinfo=timezone.utc)


def test_heatmap_clips_observations_and_reports_missing_minutes(fixture_db):
    conn, ids, actors, context = fixture_db
    context(actors["guard"])
    entry = passages.start_attempt(
        conn, actors["guard"], ids["session"], ids["qr_token"], "entry"
    )
    conn.execute(
        "INSERT INTO device_confirmations(attempt_id,employee_id,device_key_id,signature) VALUES(%s,%s,%s,%s)",
        (entry["id"], ids["employee"], ids["key"], "test"),
    )
    shift = passages.finalize(conn, actors["guard"], entry["id"], {"passed": True})[
        "shift_id"
    ]
    context(actors["admin"])
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    conn.execute(
        "UPDATE shifts SET opened_at=%s,closed_at=%s WHERE id=%s", (start, end, shift)
    )
    conn.execute(
        "INSERT INTO location_checks(shift_id,employee_id,site_id,sample_id,measured_at,received_at,position,accuracy_m,inside) VALUES(%s,%s,%s,%s,%s,%s,ST_SetSRID(ST_MakePoint(37.5,55.5),4326),5,true)",
        (shift, ids["employee"], ids["a"], uuid4(), start, start),
    )
    from app.analytics import heatmap

    coverage = heatmap(conn, ids["a"], start, end)["coverage"]
    assert coverage["observed_person_minutes"] == 10
    assert coverage["unknown_person_minutes"] == 20
    clipped = heatmap(
        conn, ids["a"], start + timedelta(minutes=5), start + timedelta(minutes=15)
    )["coverage"]
    assert (
        clipped["observed_person_minutes"] == 5
        and clipped["unknown_person_minutes"] == 5
    )
