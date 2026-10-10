from datetime import datetime, timezone
from fastapi import HTTPException
from .db import one, own_employee, audit


def notify_alert(conn, site_id, employee, shift_id, kind, detail):
    alert = one(
        conn,
        "INSERT INTO alerts(site_id,employee_id,shift_id,kind,detail) VALUES(%s,%s,%s,%s,%s) RETURNING id",
        (site_id, employee["id"], shift_id, kind, detail),
    )
    conn.execute("SELECT queue_alert_notifications(%s)", (alert["id"],))
    return alert["id"]


def record_location(conn, actor, data):
    employee = own_employee(conn, actor)
    shift = one(
        conn,
        "SELECT * FROM shifts WHERE id=%s AND employee_id=%s FOR UPDATE",
        (data.shift_id, employee["id"]),
    )
    if not shift or shift["closed_at"] is not None:
        raise HTTPException(409, "Смена закрыта: координаты не принимаются")
    now = datetime.now(timezone.utc)
    if (
        data.measured_at < shift["opened_at"]
        or abs((now - data.measured_at).total_seconds()) > 120
    ):
        raise HTTPException(422, "Координаты получены вне допустимого времени")
    existing = one(
        conn,
        "SELECT * FROM location_checks WHERE shift_id=%s AND sample_id=%s",
        (shift["id"], data.sample_id),
    )
    if existing:
        return {
            "id": existing["id"],
            "inside": existing["inside"],
            "suspicious": existing["suspicious"],
        }
    last = one(
        conn,
        "SELECT * FROM location_checks WHERE shift_id=%s ORDER BY received_at DESC LIMIT 1",
        (shift["id"],),
    )
    if last and (now - last["received_at"]).total_seconds() < 590:
        raise HTTPException(429, "Геопроверка выполняется раз в 10 минут")
    point = one(
        conn,
        "SELECT ST_Covers(boundary,ST_SetSRID(ST_MakePoint(%s,%s),4326)) AS inside FROM sites WHERE id=%s",
        (data.longitude, data.latitude, shift["site_id"]),
    )
    zone = one(
        conn,
        "SELECT z.id FROM zone_shapes z JOIN site_map_versions v ON v.id=z.map_version_id WHERE v.site_id=%s AND v.version=(SELECT max(version) FROM site_map_versions WHERE site_id=%s) AND ST_Covers(z.polygon,ST_SetSRID(ST_MakePoint(%s,%s),4326)) LIMIT 1",
        (shift["site_id"], shift["site_id"], data.longitude, data.latitude),
    )
    suspicious = data.accuracy_m > 100
    if last:
        distance = one(
            conn,
            "SELECT ST_Distance(position::geography,ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography) AS m FROM location_checks WHERE id=%s",
            (data.longitude, data.latitude, last["id"]),
        )["m"]
        suspicious |= (
            distance / max((now - last["received_at"]).total_seconds(), 1) > 50
        )
    row = one(
        conn,
        "INSERT INTO location_checks(shift_id,employee_id,site_id,sample_id,measured_at,position,accuracy_m,inside,zone_shape_id,suspicious) VALUES(%s,%s,%s,%s,%s,ST_SetSRID(ST_MakePoint(%s,%s),4326),%s,%s,%s,%s) RETURNING id",
        (
            shift["id"],
            employee["id"],
            shift["site_id"],
            data.sample_id,
            data.measured_at,
            data.longitude,
            data.latitude,
            data.accuracy_m,
            point["inside"],
            zone["id"] if zone else None,
            suspicious,
        ),
    )
    if not point["inside"] and (not last or last["inside"]):
        notify_alert(
            conn,
            shift["site_id"],
            employee,
            shift["id"],
            "outside",
            "Сотрудник вышел за границу объекта",
        )
        audit(
            conn,
            actor,
            "outside_zone",
            shift["site_id"],
            employee["id"],
            shift_id=str(shift["id"]),
        )
    return {"id": row["id"], "inside": point["inside"], "suspicious": suspicious}
