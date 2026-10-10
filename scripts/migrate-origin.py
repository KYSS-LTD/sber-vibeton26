"""Move an existing realm/app to a new HTTPS origin during maintenance.

Run in the Keycloak network namespace using the secrets image and a read-only
bootstrap_secrets mount. Credentials are read from files and never printed.
Without --apply this validates the migration and prints counts only.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
from psycopg.types.json import Jsonb


def origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("Expected a plain HTTPS origin")
    return value.rstrip("/")


def migrate(old_origin, new_origin, apply=False):
    if old_origin == new_origin:
        raise ValueError("Old and new origins must differ")
    old_issuer = old_origin + "/auth/realms/prohod"
    new_issuer = new_origin + "/auth/realms/prohod"
    bootstrap = Path("/bootstrap")
    with httpx.Client(base_url="http://127.0.0.1:8080/auth", timeout=30) as iam:
        login = iam.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "client_id": "admin-cli",
                "grant_type": "password",
                "username": "kcadmin",
                "password": (bootstrap / "kc_admin").read_text().strip(),
            },
        )
        login.raise_for_status()
        iam.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        clients = iam.get(
            "/admin/realms/prohod/clients", params={"clientId": "prohod-web"}
        )
        clients.raise_for_status()
        if len(clients.json()) != 1:
            raise ValueError("Expected exactly one prohod-web client")
        client_id = clients.json()[0]["id"]
        path = f"/admin/realms/prohod/clients/{client_id}"
        current = iam.get(path)
        current.raise_for_status()
        previous = current.json()
        updated = dict(previous)
        updated.update(
            redirectUris=[new_origin + "/*"],
            webOrigins=[new_origin],
            rootUrl=new_origin,
            baseUrl=new_origin,
        )
        updated["attributes"] = {
            **previous.get("attributes", {}),
            "post.logout.redirect.uris": new_origin + "/*",
        }
        client_changed = False
        try:
            with psycopg.connect(
                host="postgres",
                dbname="prohod",
                user="prohod_owner",
                password=(bootstrap / "db_owner").read_text().strip(),
            ) as conn:
                conn.execute("SET LOCAL lock_timeout='10s'")
                conn.execute("LOCK TABLE principals IN SHARE ROW EXCLUSIVE MODE")
                collision = conn.execute(
                    "SELECT count(*) FROM principals a JOIN principals b ON a.subject=b.subject "
                    "WHERE a.issuer=%s AND b.issuer=%s AND a.id<>b.id",
                    (old_issuer, new_issuer),
                ).fetchone()[0]
                if collision:
                    raise ValueError("Principal collision; migration refused")
                ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM principals WHERE issuer=%s", (old_issuer,)
                    )
                ]
                employees = [
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM employees WHERE principal_id=ANY(%s)", (ids,)
                    )
                ]
                stats = {"principals": len(ids), "employees": len(employees)}
                for table, column, values in (
                    ("employee_device_keys", "employee_id", employees),
                    ("daily_qr_credentials", "employee_id", employees),
                    ("gate_sessions", "controller_id", ids),
                ):
                    stats[table] = conn.execute(
                        f"SELECT count(*) FROM {table} WHERE {column}=ANY(%s) AND revoked_at IS NULL",
                        (values,),
                    ).fetchone()[0]
                if not apply:
                    print(json.dumps({"apply": False, **stats}))
                    return
                conn.execute(
                    "UPDATE principals SET issuer=%s WHERE issuer=%s",
                    (new_issuer, old_issuer),
                )
                for table, column, values in (
                    ("employee_device_keys", "employee_id", employees),
                    ("daily_qr_credentials", "employee_id", employees),
                    ("gate_sessions", "controller_id", ids),
                ):
                    conn.execute(
                        f"UPDATE {table} SET revoked_at=now() WHERE {column}=ANY(%s) AND revoked_at IS NULL",
                        (values,),
                    )
                conn.execute(
                    "UPDATE passage_attempts SET state='cancelled',reason='origin_changed',finished_at=now() "
                    "WHERE employee_id=ANY(%s) AND state='pending'",
                    (employees,),
                )
                if ids:
                    conn.execute(
                        "INSERT INTO audit_events(action,detail) VALUES('origin_changed',%s)",
                        (
                            Jsonb(
                                {
                                    "old_origin": old_origin,
                                    "new_origin": new_origin,
                                    **stats,
                                }
                            ),
                        ),
                    )
                response = iam.put(path, json=updated)
                response.raise_for_status()
                client_changed = True
            print(json.dumps({"apply": True, **stats}))
        except Exception:
            if client_changed:
                rollback = iam.put(path, json=previous)
                rollback.raise_for_status()
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_origin", type=origin)
    parser.add_argument("new_origin", type=origin)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    migrate(args.old_origin, args.new_origin, args.apply)
