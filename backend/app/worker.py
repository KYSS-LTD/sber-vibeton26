import json
import time
from psycopg.types.json import Jsonb
from pywebpush import webpush, WebPushException
from .config import SECRET_DIR
from .db import transaction, SYSTEM, many, one
from .geo import notify_alert
from .passages import expire


def tick():
    with transaction(SYSTEM) as conn:
        expire(conn)
        stale = many(
            conn,
            """SELECT s.*,e.principal_id FROM shifts s JOIN employees e ON e.id=s.employee_id
          LEFT JOIN LATERAL(SELECT received_at FROM location_checks WHERE shift_id=s.id ORDER BY received_at DESC LIMIT 1) l ON true
          WHERE s.closed_at IS NULL AND coalesce(l.received_at,s.opened_at)<now()-interval '12 minutes'
          AND NOT EXISTS(SELECT 1 FROM alerts a WHERE a.shift_id=s.id AND a.kind='location_lost' AND a.created_at>=coalesce(l.received_at,s.opened_at))""",
        )
        for shift in stale:
            # Lock the same shift row as GPS/exit; no stale alert after a committed closure.
            current = one(
                conn,
                "SELECT * FROM shifts WHERE id=%s AND closed_at IS NULL FOR UPDATE",
                (shift["id"],),
            )
            if current:
                notify_alert(
                    conn,
                    shift["site_id"],
                    {"id": shift["employee_id"], "principal_id": shift["principal_id"]},
                    shift["id"],
                    "location_lost",
                    "Нет свежей геопозиции более 12 минут",
                )
        scheduled = many(
            conn,
            "SELECT s.*,e.principal_id FROM scheduled_shifts s JOIN employees e ON e.id=s.employee_id WHERE notified_at IS NULL AND starts_at<=now()+interval '15 minutes' AND starts_at>now() FOR UPDATE OF s SKIP LOCKED",
        )
        for schedule in scheduled:
            conn.execute(
                "INSERT INTO notifications(principal_id,site_id,kind,payload) VALUES(%s,%s,'schedule',%s)",
                (
                    schedule["principal_id"],
                    schedule["site_id"],
                    Jsonb(
                        {
                            "detail": "Смена начнётся через 15 минут",
                            "starts_at": schedule["starts_at"].isoformat(),
                        }
                    ),
                ),
            )
            conn.execute(
                "UPDATE scheduled_shifts SET notified_at=now() WHERE id=%s",
                (schedule["id"],),
            )
        jobs = many(
            conn,
            "SELECT * FROM jobs WHERE kind='notify_alert' AND state='pending' FOR UPDATE SKIP LOCKED",
        )
        for job in jobs:
            for principal_id in job["payload"]["principal_ids"]:
                conn.execute(
                    "INSERT INTO notifications(principal_id,site_id,kind,payload) VALUES(%s,%s,'alert',%s)",
                    (
                        principal_id,
                        job["payload"]["site_id"],
                        Jsonb(
                            {
                                "detail": job["payload"]["detail"],
                                "alert_id": job["payload"]["alert_id"],
                            }
                        ),
                    ),
                )
            conn.execute(
                "UPDATE jobs SET state='done',updated_at=now() WHERE id=%s",
                (job["id"],),
            )
        pending = many(
            conn,
            "SELECT * FROM notifications WHERE pushed_at IS NULL ORDER BY created_at LIMIT 30 FOR UPDATE SKIP LOCKED",
        )
        for notification in pending:
            subscriptions = many(
                conn,
                "SELECT * FROM push_subscriptions WHERE principal_id=%s",
                (notification["principal_id"],),
            )
            for subscription in subscriptions:
                try:
                    webpush(
                        subscription_info=subscription["subscription"],
                        data=json.dumps(
                            {
                                "title": "Проход",
                                "body": notification["payload"].get(
                                    "detail", notification["kind"]
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        vapid_private_key=str(SECRET_DIR / "vapid_private"),
                        vapid_claims={"sub": "mailto:operations@example.invalid"},
                        timeout=10,
                    )
                except WebPushException as e:
                    print(
                        "Push failed:",
                        e.response.status_code if e.response else "network",
                        flush=True,
                    )
            conn.execute(
                "UPDATE notifications SET pushed_at=now() WHERE id=%s",
                (notification["id"],),
            )


if __name__ == "__main__":
    while True:
        try:
            tick()
        except Exception as e:
            print("Worker retry:", type(e).__name__, flush=True)
        time.sleep(10)
