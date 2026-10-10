from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi import HTTPException
from .config import dsn


@dataclass(frozen=True)
class Actor:
    id: UUID
    roles: frozenset[str]
    username: str = ""


SYSTEM = Actor(
    UUID("00000000-0000-0000-0000-000000000001"), frozenset({"admin"}), "system"
)


@contextmanager
def transaction(actor: Actor, analytics=False, owner=False):
    with psycopg.connect(
        dsn("owner" if owner else "analytics" if analytics else "app"),
        row_factory=dict_row,
    ) as conn:
        if not owner:
            conn.execute(
                "SELECT set_config('app.actor',%s,true),set_config('app.roles',%s,true)",
                (str(actor.id), "".join("|" + r + "|" for r in sorted(actor.roles))),
            )
        yield conn


def one(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def many(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


def require_roles(actor, *roles):
    if not actor.roles.intersection(roles):
        raise HTTPException(403, "Недостаточно прав")


def authorize_site(conn, actor, site_id, mode="read"):
    site = one(
        conn,
        "SELECT *,ST_AsGeoJSON(boundary)::json AS boundary_geojson FROM sites WHERE id=%s",
        (site_id,),
    )
    if not site:
        raise HTTPException(404, "Объект недоступен")
    if mode == "gate":
        require_roles(actor, "controller")
        if not one(
            conn,
            "SELECT 1 FROM controller_assignments WHERE principal_id=%s AND site_id=%s AND revoked_at IS NULL",
            (actor.id, site_id),
        ):
            raise HTTPException(403, "Вахтёр назначен на другой объект")
    elif mode == "manager":
        require_roles(actor, "manager", "admin")
        if "admin" not in actor.roles and not one(
            conn,
            "SELECT 1 FROM site_memberships WHERE principal_id=%s AND site_id=%s AND kind='manager' AND active",
            (actor.id, site_id),
        ):
            raise HTTPException(403, "Объект не назначен руководителю")
    return site


def own_employee(conn, actor):
    row = one(
        conn, "SELECT * FROM employees WHERE principal_id=%s AND active", (actor.id,)
    )
    if not row:
        raise HTTPException(403, "Активный сотрудник не найден")
    return row


def authorize_staff_site(conn, actor, site_id):
    """Read scope is the union of roles; a gate operation always uses mode=gate."""
    if "controller" in actor.roles and one(
        conn,
        "SELECT 1 FROM controller_assignments WHERE principal_id=%s AND site_id=%s AND revoked_at IS NULL",
        (actor.id, site_id),
    ):
        return authorize_site(conn, actor, site_id, "gate")
    return authorize_site(conn, actor, site_id, "manager")


def audit(conn, actor, action, site_id=None, employee_id=None, **detail):
    conn.execute(
        "INSERT INTO audit_events(principal_id,site_id,employee_id,action,detail) VALUES(%s,%s,%s,%s,%s)",
        (actor.id, site_id, employee_id, action, Jsonb(detail)),
    )
