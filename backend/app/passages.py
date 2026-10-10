import secrets
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
from uuid import uuid4
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from cryptography.exceptions import InvalidSignature
from .db import one, audit, authorize_site, own_employee
from .security import digest, encrypt, decrypt, canonical_challenge, verify_device


def utcnow():
    return datetime.now(timezone.utc)


def expire(conn):
    conn.execute(
        "UPDATE passage_attempts SET state='expired',reason='Истекли 30 секунд',finished_at=now() WHERE state='pending' AND deadline<=now()"
    )


def daily_qr(conn, actor, site_id):
    employee = own_employee(conn, actor)
    conn.execute("SELECT id FROM employees WHERE id=%s FOR UPDATE", (employee["id"],))
    site = authorize_site(conn, actor, site_id)
    if not site["active"] or not one(
        conn,
        "SELECT 1 FROM site_memberships WHERE principal_id=%s AND site_id=%s AND kind='employee' AND active",
        (actor.id, site_id),
    ):
        raise HTTPException(403, "Нет назначения на объект")
    if not one(
        conn,
        "SELECT 1 FROM face_reference_sets WHERE employee_id=%s AND state='active'",
        (employee["id"],),
    ):
        raise HTTPException(409, "Администратор должен добавить три эталонных снимка")
    if not one(
        conn,
        "SELECT 1 FROM employee_device_keys WHERE employee_id=%s AND revoked_at IS NULL",
        (employee["id"],),
    ):
        raise HTTPException(409, "Сначала зарегистрируйте этот браузер")
    today = utcnow().astimezone(ZoneInfo(site["timezone"])).date()
    row = one(
        conn,
        "SELECT * FROM daily_qr_credentials WHERE employee_id=%s AND site_id=%s AND local_date=%s AND revoked_at IS NULL",
        (employee["id"], site_id, today),
    )
    if not row:
        token = secrets.token_urlsafe(32)
        deadline = datetime.combine(
            today + timedelta(days=1), time.min, tzinfo=ZoneInfo(site["timezone"])
        ).astimezone(timezone.utc)
        row = one(
            conn,
            "INSERT INTO daily_qr_credentials(employee_id,site_id,local_date,token_hash,token_encrypted,expires_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
            (employee["id"], site_id, today, digest(token), encrypt(token), deadline),
        )
    return {
        "credential_id": row["id"],
        "token": decrypt(row["token_encrypted"]),
        "expires_at": row["expires_at"],
        "employee_id": employee["id"],
        "site_id": site_id,
    }


def start_attempt(conn, actor, session_id, token, direction):
    gate = one(
        conn,
        "SELECT * FROM gate_sessions WHERE id=%s AND controller_id=%s AND revoked_at IS NULL",
        (session_id, actor.id),
    )
    if not gate:
        raise HTTPException(403, "Сессия КПП недоступна")
    site = authorize_site(conn, actor, gate["site_id"], "gate")
    if not site["active"]:
        raise HTTPException(409, "Объект не активен")
    qr = one(
        conn,
        "SELECT * FROM daily_qr_credentials WHERE token_hash=%s AND site_id=%s AND revoked_at IS NULL AND expires_at>now()",
        (digest(token), gate["site_id"]),
    )
    if not qr:
        raise HTTPException(403, "QR недействителен для этого объекта")
    employee = one(
        conn,
        "SELECT * FROM employees WHERE id=%s AND active FOR UPDATE",
        (qr["employee_id"],),
    )
    if not employee or not one(
        conn,
        "SELECT 1 FROM site_memberships WHERE principal_id=%s AND site_id=%s AND kind='employee' AND active",
        (employee["principal_id"], gate["site_id"]),
    ):
        raise HTTPException(403, "Сотрудник заблокирован или не назначен на объект")
    expire(conn)
    if one(
        conn,
        "SELECT 1 FROM passage_attempts WHERE state='pending' AND (employee_id=%s OR gate_session_id=%s)",
        (employee["id"], session_id),
    ):
        raise HTTPException(409, "Уже идёт проверка прохода")
    shift = one(
        conn,
        "SELECT * FROM shifts WHERE employee_id=%s AND closed_at IS NULL",
        (employee["id"],),
    )
    if direction == "entry" and shift:
        raise HTTPException(409, "У сотрудника уже открыта смена")
    if direction == "exit" and (not shift or shift["site_id"] != gate["site_id"]):
        raise HTTPException(409, "Нет открытой смены на этом объекте")
    reference = one(
        conn,
        "SELECT * FROM face_reference_sets WHERE employee_id=%s AND state='active'",
        (employee["id"],),
    )
    key = one(
        conn,
        "SELECT * FROM employee_device_keys WHERE employee_id=%s AND revoked_at IS NULL",
        (employee["id"],),
    )
    if not reference or not key:
        raise HTTPException(409, "Не готовы эталоны или устройство")
    attempt_id = uuid4()
    nonce = secrets.token_urlsafe(32)
    deadline = utcnow() + timedelta(seconds=30)
    actions = secrets.choice([["left", "right"], ["right", "left"]])
    challenge = canonical_challenge(
        attempt_id=str(attempt_id),
        employee_id=str(employee["id"]),
        site_id=str(gate["site_id"]),
        checkpoint_id=str(gate["checkpoint_id"]),
        gate_session_id=str(session_id),
        device_key_id=str(key["id"]),
        credential_id=str(qr["id"]),
        direction=direction,
        nonce=nonce,
        deadline=deadline.isoformat(),
    )
    row = one(
        conn,
        "INSERT INTO passage_attempts(id,site_id,employee_id,qr_id,gate_session_id,reference_set_id,device_key_id,direction,nonce,challenge,actions,deadline) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (
            attempt_id,
            gate["site_id"],
            employee["id"],
            qr["id"],
            session_id,
            reference["id"],
            key["id"],
            direction,
            nonce,
            challenge,
            actions,
            deadline,
        ),
    )
    audit(
        conn,
        actor,
        "passage_started",
        gate["site_id"],
        employee["id"],
        attempt_id=str(attempt_id),
        direction=direction,
    )
    return public_attempt(row, employee["full_name"])


def public_attempt(row, full_name=None):
    return {
        k: row[k]
        for k in [
            "id",
            "employee_id",
            "site_id",
            "direction",
            "actions",
            "deadline",
            "state",
            "reason",
            "shift_id",
        ]
    } | {"full_name": full_name}


def confirm_device(conn, actor, attempt_id, signature):
    employee = own_employee(conn, actor)
    conn.execute("SELECT id FROM employees WHERE id=%s FOR UPDATE", (employee["id"],))
    attempt = one(
        conn,
        "SELECT * FROM passage_attempts WHERE id=%s AND employee_id=%s FOR UPDATE",
        (attempt_id, employee["id"]),
    )
    if not attempt or attempt["state"] != "pending" or attempt["deadline"] <= utcnow():
        raise HTTPException(409, "Запрос истёк или отменён")
    qr = one(
        conn,
        "SELECT 1 FROM daily_qr_credentials WHERE id=%s AND revoked_at IS NULL AND expires_at>now()",
        (attempt["qr_id"],),
    )
    key = one(
        conn,
        "SELECT * FROM employee_device_keys WHERE id=%s AND revoked_at IS NULL",
        (attempt["device_key_id"],),
    )
    if not qr or not key:
        raise HTTPException(409, "QR или устройство отозваны")
    try:
        verify_device(key["public_jwk"], attempt["challenge"], signature)
    except (InvalidSignature, ValueError, KeyError):
        raise HTTPException(403, "Подпись устройства не прошла проверку")
    existing = one(
        conn, "SELECT * FROM device_confirmations WHERE attempt_id=%s", (attempt_id,)
    )
    if existing:
        if existing["signature"] != signature:
            raise HTTPException(409, "Попытка уже подтверждена")
    else:
        conn.execute(
            "INSERT INTO device_confirmations(attempt_id,employee_id,device_key_id,signature) VALUES(%s,%s,%s,%s)",
            (attempt_id, employee["id"], key["id"], signature),
        )
    return {"confirmed": True}


def finalize(conn, actor, attempt_id, vision_result, media_id=None):
    initial = one(conn, "SELECT * FROM passage_attempts WHERE id=%s", (attempt_id,))
    if not initial:
        raise HTTPException(404, "Проверка недоступна")
    conn.execute(
        "SELECT id FROM employees WHERE id=%s FOR UPDATE", (initial["employee_id"],)
    )
    attempt = one(
        conn, "SELECT * FROM passage_attempts WHERE id=%s FOR UPDATE", (attempt_id,)
    )
    gate = one(
        conn,
        "SELECT * FROM gate_sessions WHERE id=%s AND controller_id=%s AND revoked_at IS NULL",
        (attempt["gate_session_id"], actor.id),
    )
    if not gate:
        raise HTTPException(403, "Сессия КПП отозвана")
    authorize_site(conn, actor, attempt["site_id"], "gate")
    if attempt["state"] != "pending":
        return public_attempt(attempt)
    reason = None
    if utcnow() > attempt["deadline"]:
        reason = "Истекли 30 секунд"
    elif not one(
        conn,
        "SELECT 1 FROM daily_qr_credentials WHERE id=%s AND revoked_at IS NULL AND expires_at>now()",
        (attempt["qr_id"],),
    ):
        reason = "QR отозван или истёк"
    elif not one(
        conn,
        "SELECT 1 FROM employee_device_keys WHERE id=%s AND revoked_at IS NULL",
        (attempt["device_key_id"],),
    ):
        reason = "Устройство отозвано"
    elif not one(
        conn,
        "SELECT 1 FROM face_reference_sets WHERE id=%s AND state='active'",
        (attempt["reference_set_id"],),
    ):
        reason = "Эталоны изменились"
    elif not one(
        conn,
        "SELECT 1 FROM device_confirmations WHERE attempt_id=%s AND device_key_id=%s",
        (attempt_id, attempt["device_key_id"]),
    ):
        reason = "Телефон не подтвердил запрос"
    elif not vision_result.get("passed"):
        reason = vision_result.get("reason", "Биометрия не прошла проверку")
    employee = one(
        conn,
        "SELECT * FROM employees WHERE id=%s AND active",
        (attempt["employee_id"],),
    )
    if not employee or not one(
        conn,
        "SELECT 1 FROM site_memberships WHERE principal_id=%s AND site_id=%s AND kind='employee' AND active",
        (employee["principal_id"], attempt["site_id"]),
    ):
        reason = "Сотрудник заблокирован или назначение отозвано"
    shift = one(
        conn,
        "SELECT * FROM shifts WHERE employee_id=%s AND closed_at IS NULL FOR UPDATE",
        (attempt["employee_id"],),
    )
    shift_id = None
    if not reason:
        if attempt["direction"] == "entry":
            if shift:
                reason = "Уже открыта другая смена"
            else:
                shift_id = uuid4()
                conn.execute(
                    "INSERT INTO shifts(id,employee_id,site_id,entry_attempt_id) VALUES(%s,%s,%s,%s)",
                    (shift_id, attempt["employee_id"], attempt["site_id"], attempt_id),
                )
        elif not shift or shift["site_id"] != attempt["site_id"]:
            reason = "Открытая смена не найдена"
        else:
            shift_id = shift["id"]
            conn.execute(
                "UPDATE shifts SET closed_at=now(),exit_attempt_id=%s WHERE id=%s",
                (attempt_id, shift_id),
            )
    state = "denied" if reason else "allowed"
    row = one(
        conn,
        "UPDATE passage_attempts SET state=%s,reason=%s,vision_result=%s,evidence_media_id=%s,shift_id=%s,finished_at=now() WHERE id=%s RETURNING *",
        (state, reason, Jsonb(vision_result), media_id, shift_id, attempt_id),
    )
    audit(
        conn,
        actor,
        "passage_" + state,
        attempt["site_id"],
        attempt["employee_id"],
        attempt_id=str(attempt_id),
        reason=reason,
    )
    if not reason:
        conn.execute("SELECT queue_shift_notification(%s)", (shift_id,))
    return public_attempt(row)
