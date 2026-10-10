import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import json
import logging
import math
from typing import Annotated
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import httpx
import psycopg
from psycopg.types.json import Jsonb
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Request, Query
from fastapi.responses import Response, StreamingResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, ConfigDict, field_validator
from .auth import authenticate
from .db import (
    Actor,
    SYSTEM,
    transaction,
    one,
    many,
    require_roles,
    authorize_site,
    authorize_staff_site,
    own_employee,
    audit,
)
from .config import OIDC_INTERNAL, secret, MODEL_DIR, ISSUER
from .security import digest
from . import passages, analytics, storage, geo, llm, vision

log = logging.getLogger("prohod")


@asynccontextmanager
async def lifespan(app):
    # Preload all CPU models before accepting camera requests.
    if (MODEL_DIR / "minifas.onnx").exists():
        vision.engine()
    yield


app = FastAPI(
    title="Проход",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/v1/openapi.json",
)
Auth = Annotated[Actor, Depends(authenticate)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


def validate_polygon(value):
    if set(value) != {"type", "coordinates"} or value.get("type") != "Polygon":
        raise ValueError("Expected GeoJSON Polygon")
    rings = value.get("coordinates")
    if not isinstance(rings, list) or not 1 <= len(rings) <= 16:
        raise ValueError("Invalid polygon rings")
    for ring in rings:
        if (
            not isinstance(ring, list)
            or not 4 <= len(ring) <= 1000
            or ring[0] != ring[-1]
        ):
            raise ValueError("Polygon rings must be closed")
        for point in ring:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or any(
                    type(v) not in (int, float) or not math.isfinite(v) for v in point
                )
                or not -180 <= point[0] <= 180
                or not -90 <= point[1] <= 90
            ):
                raise ValueError("Invalid WGS84 coordinates")
    return value


class SiteInput(Input):
    name: str = Field(min_length=2, max_length=120)
    timezone: str = "Europe/Moscow"
    boundary: dict
    _boundary = field_validator("boundary")(validate_polygon)

    @field_validator("timezone")
    @classmethod
    def valid_tz(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Unknown timezone")
        return value


class AssignmentInput(Input):
    principal_id: UUID
    site_id: UUID
    kind: str = Field(pattern="^(controller|manager|employee)$")
    active: bool = True


class PersonInput(Input):
    username: str = Field(pattern="^[a-zA-Z0-9._-]{3,64}$")
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)
    personnel_number: str = Field(min_length=1, max_length=40)
    role: str = Field(pattern="^(employee|controller|manager|admin)$")


class ActiveInput(Input):
    active: bool


class KeyInput(Input):
    public_jwk: dict


class GateInput(Input):
    checkpoint_id: UUID


class ScanInput(Input):
    session_id: UUID
    token: str = Field(min_length=20, max_length=100)
    direction: str = Field(pattern="^(entry|exit)$")


class ConfirmationInput(Input):
    signature: str = Field(min_length=80, max_length=100)


class GeoInput(Input):
    shift_id: UUID
    sample_id: UUID
    measured_at: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(ge=0, le=1000)

    @field_validator("measured_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone is required")
        return value


class ZoneInput(Input):
    name: str = Field(min_length=1, max_length=100)
    polygon: dict
    _polygon = field_validator("polygon")(validate_polygon)


class MapInput(Input):
    transform: dict
    zones: list[ZoneInput] = Field(max_length=100)
    media_id: UUID | None = None


class ScheduleInput(Input):
    employee_id: UUID
    site_id: UUID
    starts_at: datetime
    ends_at: datetime


class ModelInput(Input):
    provider: str = Field(pattern="^(ollama|gigachat)$")


class AssistantInput(Input):
    site_id: UUID
    question: str = Field(min_length=2, max_length=1500)
    start: datetime
    end: datetime
    employee_ids: list[UUID] | None = Field(default=None, max_length=100)


class PushInput(Input):
    subscription: dict


@app.exception_handler(psycopg.errors.UniqueViolation)
async def conflict_handler(request, exc):
    return JSONResponse(
        status_code=409,
        content={
            "detail": "Запись уже существует или выполняется параллельная операция"
        },
    )


@app.exception_handler(psycopg.errors.CheckViolation)
async def constraint_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"detail": "Данные не соответствуют ограничениям схемы"},
    )


@app.exception_handler(psycopg.errors.InsufficientPrivilege)
async def denied_handler(request, exc):
    return JSONResponse(
        status_code=403, content={"detail": "База данных отклонила доступ"}
    )


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    actor = getattr(request.state, "actor", None)
    if actor and request.method != "GET" and exc.status_code >= 400:
        try:
            with transaction(actor) as conn:
                audit(
                    conn,
                    actor,
                    "request_denied",
                    method=request.method,
                    path=request.url.path,
                    status=exc.status_code,
                    reason=str(exc.detail)[:200],
                )
        except Exception:
            log.error("Failed to persist denied-request audit")
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
    )


@app.get("/api/v1/health")
def health():
    with transaction(SYSTEM) as conn:
        one(conn, "SELECT 1")
    return {"status": "ok", "models_ready": (MODEL_DIR / "minifas.onnx").exists()}


@app.get("/api/v1/me")
def me(actor: Auth):
    with transaction(actor) as conn:
        employee = one(
            conn, "SELECT * FROM employees WHERE principal_id=%s", (actor.id,)
        )
        sites = many(
            conn,
            "SELECT id,name,active,timezone,ST_AsGeoJSON(boundary)::json AS boundary FROM sites ORDER BY name",
        )
        assignment = one(
            conn,
            "SELECT site_id FROM controller_assignments WHERE principal_id=%s AND revoked_at IS NULL",
            (actor.id,),
        )
    return {
        "id": actor.id,
        "username": actor.username,
        "roles": sorted(actor.roles),
        "employee": employee,
        "sites": sites,
        "controller_site_id": assignment["site_id"] if assignment else None,
    }


@app.get("/api/v1/people")
def people(actor: Auth, site_id: UUID | None = None):
    require_roles(actor, "admin", "manager", "controller")
    with transaction(actor) as conn:
        if site_id:
            authorize_staff_site(conn, actor, site_id)
        return many(
            conn,
            """SELECT p.id AS principal_id,p.username,p.roles,p.active,e.id AS employee_id,e.full_name,e.personnel_number,
            (SELECT count(*) FROM face_reference_photos f JOIN face_reference_sets r ON r.id=f.set_id WHERE r.employee_id=e.id AND r.state='active') AS reference_count,
            (SELECT site_id FROM controller_assignments c WHERE c.principal_id=p.id AND revoked_at IS NULL) AS controller_site_id
            FROM principals p LEFT JOIN employees e ON e.principal_id=p.id WHERE p.username<>'system'
            AND (%s::uuid IS NULL OR EXISTS(SELECT 1 FROM site_memberships m WHERE m.principal_id=p.id AND site_id=%s AND active)
            OR EXISTS(SELECT 1 FROM controller_assignments c WHERE c.principal_id=p.id AND site_id=%s AND revoked_at IS NULL)) ORDER BY p.username""",
            (site_id, site_id, site_id),
        )


def management_request(method, path, **kwargs):
    base = OIDC_INTERNAL.split("/realms/")[0]
    with httpx.Client(timeout=15) as client:
        token = client.post(
            OIDC_INTERNAL + "/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "prohod-management",
                "client_secret": secret("kc_service"),
            },
        )
        token.raise_for_status()
        response = client.request(
            method,
            base + "/admin/realms/prohod/" + path,
            headers={"Authorization": "Bearer " + token.json()["access_token"]},
            **kwargs,
        )
        if response.status_code == 409:
            raise HTTPException(409, "Логин уже занят в Keycloak")
        response.raise_for_status()
        return response


@app.post("/api/v1/people", status_code=201)
def create_person(data: PersonInput, actor: Auth):
    require_roles(actor, "admin")
    principal_id = uuid4()
    payload = {
        "id": str(principal_id),
        "username": data.username,
        "enabled": True,
        "firstName": data.full_name,
        "lastName": "Пользователь",
        "email": data.username + "@prohod.local",
        "emailVerified": True,
        "credentials": [
            {"type": "password", "value": data.password, "temporary": False}
        ],
    }
    try:
        created = management_request("POST", "users", json=payload)
        principal_id = UUID(created.headers["location"].rstrip("/").rsplit("/", 1)[1])
        role = management_request("GET", "roles/" + data.role).json()
        management_request(
            "POST", f"users/{principal_id}/role-mappings/realm", json=[role]
        )
        with transaction(actor) as conn:
            conn.execute(
                "INSERT INTO principals(id,issuer,subject,username,roles) VALUES(%s,%s,%s,%s,%s)",
                (principal_id, ISSUER, str(principal_id), data.username, [data.role]),
            )
            employee_id = None
            if data.role == "employee":
                employee_id = one(
                    conn,
                    "INSERT INTO employees(principal_id,full_name,personnel_number) VALUES(%s,%s,%s) RETURNING id",
                    (principal_id, data.full_name, data.personnel_number),
                )["id"]
            audit(
                conn,
                actor,
                "person_created",
                employee_id=employee_id,
                principal_id_created=str(principal_id),
                role=data.role,
            )
        return {"principal_id": principal_id, "employee_id": employee_id}
    except Exception:
        try:
            management_request("DELETE", f"users/{principal_id}")
        except Exception:
            log.error("Keycloak account cleanup failed for %s", principal_id)
        raise


@app.put("/api/v1/people/{principal_id}/active")
def activate_person(principal_id: UUID, data: ActiveInput, actor: Auth):
    require_roles(actor, "admin")
    with transaction(actor) as conn:
        row = one(
            conn,
            "UPDATE principals SET active=%s WHERE id=%s RETURNING *",
            (data.active, principal_id),
        )
        if not row:
            raise HTTPException(404, "Пользователь не найден")
        employee = one(
            conn,
            "UPDATE employees SET active=%s WHERE principal_id=%s RETURNING id",
            (data.active, principal_id),
        )
        if employee:
            conn.execute(
                "UPDATE daily_qr_credentials SET revoked_at=now() WHERE employee_id=%s AND revoked_at IS NULL",
                (employee["id"],),
            )
            conn.execute(
                "UPDATE passage_attempts SET state='cancelled',reason='Статус сотрудника изменён',finished_at=now() WHERE employee_id=%s AND state='pending'",
                (employee["id"],),
            )
        audit(
            conn, actor, "person_status", active=data.active, target=str(principal_id)
        )
    return {"active": data.active}


@app.post("/api/v1/assignments")
def assign(data: AssignmentInput, actor: Auth):
    require_roles(actor, "admin")
    with transaction(actor) as conn:
        principal = one(
            conn,
            "SELECT * FROM principals WHERE id=%s FOR UPDATE",
            (data.principal_id,),
        )
        if not principal or data.kind not in principal["roles"]:
            raise HTTPException(422, "Роль пользователя не соответствует назначению")
        authorize_site(conn, actor, data.site_id)
        if data.kind == "controller":
            conn.execute(
                "UPDATE controller_assignments SET revoked_at=now() WHERE principal_id=%s AND revoked_at IS NULL",
                (data.principal_id,),
            )
            conn.execute(
                "UPDATE passage_attempts SET state='cancelled',reason='Вахтёр переназначен',finished_at=now() WHERE state='pending' AND gate_session_id IN (SELECT id FROM gate_sessions WHERE controller_id=%s)",
                (data.principal_id,),
            )
            conn.execute(
                "UPDATE gate_sessions SET revoked_at=now() WHERE controller_id=%s AND revoked_at IS NULL",
                (data.principal_id,),
            )
            if data.active:
                conn.execute(
                    "INSERT INTO controller_assignments(principal_id,site_id) VALUES(%s,%s)",
                    (data.principal_id, data.site_id),
                )
        else:
            employee = one(
                conn,
                "SELECT id FROM employees WHERE principal_id=%s FOR UPDATE",
                (data.principal_id,),
            )
            conn.execute(
                "INSERT INTO site_memberships(principal_id,site_id,kind,active) VALUES(%s,%s,%s,%s) ON CONFLICT(principal_id,site_id,kind) DO UPDATE SET active=excluded.active",
                (data.principal_id, data.site_id, data.kind, data.active),
            )
            if employee:
                conn.execute(
                    "UPDATE daily_qr_credentials SET revoked_at=now() WHERE employee_id=%s AND site_id=%s AND revoked_at IS NULL",
                    (employee["id"], data.site_id),
                )
                conn.execute(
                    "UPDATE passage_attempts SET state='cancelled',reason='Назначение изменилось',finished_at=now() WHERE employee_id=%s AND site_id=%s AND state='pending'",
                    (employee["id"], data.site_id),
                )
        audit(
            conn,
            actor,
            "assignment_changed",
            data.site_id,
            target=str(data.principal_id),
            kind=data.kind,
            active=data.active,
        )
    return {"saved": True}


@app.get("/api/v1/sites")
def sites(actor: Auth):
    with transaction(actor) as conn:
        return many(
            conn,
            "SELECT id,name,timezone,active,ST_AsGeoJSON(boundary)::json AS boundary FROM sites ORDER BY name",
        )


@app.post("/api/v1/sites", status_code=201)
def create_site(data: SiteInput, actor: Auth):
    require_roles(actor, "admin")
    if data.boundary.get("type") != "Polygon":
        raise HTTPException(422, "Граница должна быть многоугольником")
    with transaction(actor) as conn:
        site = one(
            conn,
            "INSERT INTO sites(name,timezone,boundary) VALUES(%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)) RETURNING id",
            (data.name, data.timezone, json.dumps(data.boundary)),
        )
        conn.execute(
            "INSERT INTO checkpoints(site_id,name) VALUES(%s,%s)",
            (site["id"], "Главный КПП"),
        )
        conn.execute(
            "INSERT INTO jobs(kind,payload) VALUES('provision_site',%s)",
            (Jsonb({"site_id": str(site["id"])}),),
        )
        audit(conn, actor, "site_created", site["id"])
    return site


@app.put("/api/v1/sites/{site_id}")
def update_site(site_id: UUID, data: SiteInput, actor: Auth):
    require_roles(actor, "admin")
    with transaction(actor) as conn:
        authorize_site(conn, actor, site_id)
        conn.execute(
            "UPDATE sites SET name=%s,timezone=%s,boundary=ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) WHERE id=%s",
            (data.name, data.timezone, json.dumps(data.boundary), site_id),
        )
        conn.execute(
            "UPDATE daily_qr_credentials SET revoked_at=now() WHERE site_id=%s AND revoked_at IS NULL",
            (site_id,),
        )
        audit(conn, actor, "site_updated", site_id)
    return {"saved": True}


@app.get("/api/v1/sites/{site_id}/checkpoints")
def checkpoints(site_id: UUID, actor: Auth):
    with transaction(actor) as conn:
        authorize_site(conn, actor, site_id)
        return many(
            conn,
            "SELECT id,name,site_id FROM checkpoints WHERE site_id=%s AND active",
            (site_id,),
        )


@app.post("/api/v1/employees/{employee_id}/references")
async def references(
    employee_id: UUID, actor: Auth, photos: list[UploadFile] = File(...)
):
    require_roles(actor, "admin")
    if len(photos) != 3:
        raise HTTPException(422, "Нужны ровно три фотографии")
    contents = [await p.read(4_000_001) for p in photos]
    if len(set(digest(c) for c in contents)) != 3:
        raise HTTPException(422, "Фотографии должны различаться")
    try:
        templates, qualities = await asyncio.to_thread(vision.engine().enroll, contents)
    except vision.VisionError as e:
        raise HTTPException(422, str(e))
    with transaction(actor) as conn:
        employee = one(
            conn, "SELECT * FROM employees WHERE id=%s FOR UPDATE", (employee_id,)
        )
        if not employee:
            raise HTTPException(404, "Сотрудник не найден")
        version = one(
            conn,
            "SELECT coalesce(max(version),0)+1 AS n FROM face_reference_sets WHERE employee_id=%s",
            (employee_id,),
        )["n"]
        set_id = one(
            conn,
            "INSERT INTO face_reference_sets(employee_id,version,state,model_version) VALUES(%s,%s,'draft','sface-2021dec') RETURNING id",
            (employee_id, version),
        )["id"]
        for i, content in enumerate(contents):
            image = vision.decode_image(content)
            import cv2

            encoded = cv2.imencode(".jpg", image)[1].tobytes()
            media_id = storage.put_media(
                conn, encoded, "image/jpeg", "reference", employee_id=employee_id
            )
            photo_id = one(
                conn,
                "INSERT INTO face_reference_photos(set_id,slot,media_id,quality,image_sha256) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                (set_id, i + 1, media_id, Jsonb(qualities[i]), digest(content)),
            )["id"]
            conn.execute(
                "INSERT INTO face_templates(photo_id,embedding,model_version) VALUES(%s,%s,%s)",
                (photo_id, templates[i], "sface-2021dec"),
            )
        conn.execute(
            "UPDATE face_reference_sets SET state='retired' WHERE employee_id=%s AND state='active'",
            (employee_id,),
        )
        conn.execute(
            "UPDATE face_reference_sets SET state='active',published_at=now() WHERE id=%s",
            (set_id,),
        )
        conn.execute(
            "UPDATE passage_attempts SET state='cancelled',reason='Эталоны заменены',finished_at=now() WHERE employee_id=%s AND state='pending'",
            (employee_id,),
        )
        audit(
            conn,
            actor,
            "references_published",
            employee_id=employee_id,
            version=version,
        )
    return {"set_id": set_id, "version": version, "count": 3}


@app.post("/api/v1/me/device")
def register_device(data: KeyInput, actor: Auth):
    # Validate the curve coordinates without requiring a private key or an initial signature.
    from cryptography.hazmat.primitives.asymmetric import ec
    from .security import b64decode

    jwk = data.public_jwk
    try:
        if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256" or "d" in jwk:
            raise ValueError()
        ec.EllipticCurvePublicNumbers(
            int.from_bytes(b64decode(jwk["x"]), "big"),
            int.from_bytes(b64decode(jwk["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
    except (ValueError, KeyError):
        raise HTTPException(422, "Некорректный публичный ключ")
    with transaction(actor) as conn:
        employee = own_employee(conn, actor)
        conn.execute(
            "SELECT id FROM employees WHERE id=%s FOR UPDATE", (employee["id"],)
        )
        existing = one(
            conn,
            "SELECT * FROM employee_device_keys WHERE employee_id=%s AND revoked_at IS NULL",
            (employee["id"],),
        )
        minimal = {k: jwk[k] for k in ["kty", "crv", "x", "y"]}
        if existing:
            if existing["public_jwk"] != minimal:
                raise HTTPException(
                    409,
                    "Другой браузер уже зарегистрирован. Администратор должен сбросить устройство.",
                )
            return {"id": existing["id"]}
        row = one(
            conn,
            "INSERT INTO employee_device_keys(employee_id,public_jwk) VALUES(%s,%s) RETURNING id",
            (employee["id"], Jsonb(minimal)),
        )
        audit(conn, actor, "device_registered", employee_id=employee["id"])
        return row


@app.post("/api/v1/employees/{employee_id}/reset-device")
def reset_device(employee_id: UUID, actor: Auth):
    require_roles(actor, "admin")
    with transaction(actor) as conn:
        if not one(
            conn, "SELECT id FROM employees WHERE id=%s FOR UPDATE", (employee_id,)
        ):
            raise HTTPException(404, "Сотрудник не найден")
        conn.execute(
            "UPDATE employee_device_keys SET revoked_at=now() WHERE employee_id=%s AND revoked_at IS NULL",
            (employee_id,),
        )
        conn.execute(
            "UPDATE daily_qr_credentials SET revoked_at=now() WHERE employee_id=%s AND revoked_at IS NULL",
            (employee_id,),
        )
        conn.execute(
            "UPDATE passage_attempts SET state='cancelled',reason='Устройство сброшено',finished_at=now() WHERE employee_id=%s AND state='pending'",
            (employee_id,),
        )
        audit(conn, actor, "device_reset", employee_id=employee_id)
    return {"reset": True}


@app.get("/api/v1/me/qr")
def qr(site_id: UUID, actor: Auth):
    with transaction(actor) as conn:
        return passages.daily_qr(conn, actor, site_id)


@app.post("/api/v1/gates/sessions")
def gate_session(data: GateInput, actor: Auth):
    require_roles(actor, "controller")
    with transaction(actor) as conn:
        checkpoint = one(
            conn,
            "SELECT * FROM checkpoints WHERE id=%s AND active",
            (data.checkpoint_id,),
        )
        if not checkpoint:
            raise HTTPException(404, "КПП недоступен")
        authorize_site(conn, actor, checkpoint["site_id"], "gate")
        row = one(
            conn,
            "INSERT INTO gate_sessions(controller_id,checkpoint_id,site_id) VALUES(%s,%s,%s) RETURNING id",
            (actor.id, checkpoint["id"], checkpoint["site_id"]),
        )
        return row


@app.post("/api/v1/passages")
def scan(data: ScanInput, actor: Auth):
    with transaction(actor) as conn:
        return passages.start_attempt(
            conn, actor, data.session_id, data.token, data.direction
        )


@app.get("/api/v1/passages/{attempt_id}")
def attempt_status(attempt_id: UUID, actor: Auth):
    with transaction(actor) as conn:
        row = one(conn, "SELECT * FROM passage_attempts WHERE id=%s", (attempt_id,))
        if not row:
            raise HTTPException(404, "Попытка недоступна")
        authorize_site(conn, actor, row["site_id"], "gate")
        if not one(
            conn,
            "SELECT 1 FROM gate_sessions WHERE id=%s AND controller_id=%s",
            (row["gate_session_id"], actor.id),
        ):
            raise HTTPException(403, "Чужая сессия КПП")
        passages.expire(conn)
        row = one(conn, "SELECT * FROM passage_attempts WHERE id=%s", (attempt_id,))
        return passages.public_attempt(row)


@app.post("/api/v1/passages/{attempt_id}/confirm")
def confirm(attempt_id: UUID, data: ConfirmationInput, actor: Auth):
    with transaction(actor) as conn:
        return passages.confirm_device(conn, actor, attempt_id, data.signature)


@app.post("/api/v1/passages/{attempt_id}/verify")
async def verify(attempt_id: UUID, actor: Auth, frames: list[UploadFile] = File(...)):
    if not 8 <= len(frames) <= 32:
        raise HTTPException(422, "Нужно от 8 до 32 кадров")
    with transaction(actor) as conn:
        attempt = one(conn, "SELECT * FROM passage_attempts WHERE id=%s", (attempt_id,))
        if not attempt:
            raise HTTPException(404, "Попытка недоступна")
        authorize_site(conn, actor, attempt["site_id"], "gate")
        if not one(
            conn,
            "SELECT 1 FROM gate_sessions WHERE id=%s AND controller_id=%s AND revoked_at IS NULL",
            (attempt["gate_session_id"], actor.id),
        ):
            raise HTTPException(403, "Чужая сессия КПП")
        if attempt["state"] != "pending":
            return passages.public_attempt(attempt)
        templates = [
            r["embedding"]
            for r in many(
                conn,
                "SELECT t.embedding FROM face_templates t JOIN face_reference_photos p ON p.id=t.photo_id WHERE p.set_id=%s ORDER BY p.slot",
                (attempt["reference_set_id"],),
            )
        ]
    contents = [await f.read(4_000_001) for f in frames]
    if sum(map(len, contents)) > 24_000_000:
        raise HTTPException(413, "Слишком большой объём кадров")
    try:
        result = await asyncio.to_thread(
            vision.engine().verify, contents, templates, attempt["actions"]
        )
    except vision.VisionError as e:
        result = {"passed": False, "reason": str(e)}
    except Exception as e:
        log.error("Vision failed: %s", type(e).__name__)
        result = {"passed": False, "reason": "Сервис биометрии недоступен"}
    with transaction(actor) as conn:
        media_id = storage.put_media(
            conn,
            contents[0],
            "image/jpeg",
            "evidence",
            site_id=attempt["site_id"],
            employee_id=attempt["employee_id"],
        )
        return passages.finalize(conn, actor, attempt_id, result, media_id)


@app.get("/api/v1/me/shifts")
def my_shifts(actor: Auth):
    with transaction(actor) as conn:
        employee = own_employee(conn, actor)
        return many(
            conn,
            "SELECT s.*,x.name AS site_name FROM shifts s JOIN sites x ON x.id=s.site_id WHERE employee_id=%s ORDER BY opened_at DESC LIMIT 100",
            (employee["id"],),
        )


@app.post("/api/v1/me/location")
def location(data: GeoInput, actor: Auth):
    with transaction(actor) as conn:
        return geo.record_location(conn, actor, data)


@app.get("/api/v1/me/events")
async def events(actor: Auth, request: Request):
    async def stream():
        for _ in range(45):
            if await request.is_disconnected():
                return
            with transaction(actor) as conn:
                principal = one(
                    conn, "SELECT active FROM principals WHERE id=%s", (actor.id,)
                )
                if not principal or not principal["active"]:
                    return
                employee = one(
                    conn,
                    "SELECT id FROM employees WHERE principal_id=%s AND active",
                    (actor.id,),
                )
                shift = None
                challenges = []
                if employee:
                    shift = one(
                        conn,
                        "SELECT * FROM shifts WHERE employee_id=%s AND closed_at IS NULL",
                        (employee["id"],),
                    )
                    challenges = many(
                        conn,
                        "SELECT id,qr_id,challenge,deadline FROM passage_attempts WHERE employee_id=%s AND state='pending' AND deadline>now() AND NOT EXISTS(SELECT 1 FROM device_confirmations WHERE attempt_id=passage_attempts.id)",
                        (employee["id"],),
                    )
                notifications = many(
                    conn,
                    "SELECT id,kind,payload,created_at FROM notifications WHERE principal_id=%s AND (site_id IS NULL OR scoped_site(site_id)) ORDER BY created_at DESC LIMIT 20",
                    (actor.id,),
                )
            data = json.dumps(
                jsonable_encoder(
                    {
                        "shift": shift,
                        "challenges": challenges,
                        "notifications": notifications,
                    }
                )
            )
            yield "event: snapshot\ndata: " + data + "\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/sites/{site_id}/journal")
def journal(site_id: UUID, actor: Auth):
    with transaction(actor) as conn:
        authorize_staff_site(conn, actor, site_id)
        return many(
            conn,
            "SELECT a.id,a.direction,a.state,a.reason,a.created_at,a.finished_at,e.full_name,a.evidence_media_id FROM passage_attempts a JOIN employees e ON e.id=a.employee_id WHERE a.site_id=%s ORDER BY created_at DESC LIMIT 200",
            (site_id,),
        )


@app.get("/api/v1/sites/{site_id}/onsite")
def onsite(site_id: UUID, actor: Auth):
    with transaction(actor) as conn:
        authorize_site(conn, actor, site_id, "manager")
        return analytics.onsite(conn, site_id)


@app.get("/api/v1/sites/{site_id}/alerts")
def site_alerts(site_id: UUID, actor: Auth):
    with transaction(actor) as conn:
        authorize_site(conn, actor, site_id, "manager")
        return many(
            conn,
            "SELECT a.id,a.kind,a.detail,a.created_at,a.employee_id,e.full_name FROM alerts a JOIN employees e ON e.id=a.employee_id WHERE a.site_id=%s ORDER BY a.created_at DESC LIMIT 200",
            (site_id,),
        )


def period(start, end):
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or end <= start
        or end - start > timedelta(days=366)
    ):
        raise HTTPException(422, "Укажите период до 366 дней с часовым поясом")


@app.get("/api/v1/sites/{site_id}/analytics")
def report(
    site_id: UUID,
    actor: Auth,
    start: datetime,
    end: datetime,
    employee_ids: Annotated[list[UUID] | None, Query()] = None,
):
    period(start, end)
    with transaction(actor, analytics=True) as conn:
        authorize_site(conn, actor, site_id, "manager")
        return {
            "heatmap": analytics.heatmap(conn, site_id, start, end, employee_ids),
            "employees": analytics.statistics(conn, site_id, start, end, employee_ids),
        }


@app.get("/api/v1/sites/{site_id}/routes")
def routes(
    site_id: UUID,
    actor: Auth,
    start: datetime,
    end: datetime,
    employee_ids: Annotated[list[UUID] | None, Query()] = None,
):
    period(start, end)
    with transaction(actor, analytics=True) as conn:
        authorize_site(conn, actor, site_id, "manager")
        return many(
            conn,
            "SELECT shift_id,employee_id,received_at,ST_X(position) AS longitude,ST_Y(position) AS latitude,inside,suspicious FROM location_checks WHERE site_id=%s AND received_at BETWEEN %s AND %s AND (%s::uuid[] IS NULL OR employee_id=ANY(%s::uuid[])) ORDER BY shift_id,received_at LIMIT 10000",
            (site_id, start, end, employee_ids, employee_ids),
        )


@app.get("/api/v1/sites/{site_id}/map")
def site_map(site_id: UUID, actor: Auth):
    with transaction(actor) as conn:
        authorize_site(conn, actor, site_id)
        version = one(
            conn,
            "SELECT * FROM site_map_versions WHERE site_id=%s ORDER BY version DESC LIMIT 1",
            (site_id,),
        )
        zones = (
            []
            if not version
            else many(
                conn,
                "SELECT z.id,z.name,ST_AsGeoJSON(s.polygon)::json AS polygon FROM zones z JOIN zone_shapes s ON s.zone_id=z.id WHERE s.map_version_id=%s",
                (version["id"],),
            )
        )
        return {"version": version, "zones": zones}


@app.post("/api/v1/sites/{site_id}/map")
def save_map(site_id: UUID, data: MapInput, actor: Auth):
    with transaction(actor) as conn:
        site = authorize_site(conn, actor, site_id, "manager")
        conn.execute("SELECT id FROM sites WHERE id=%s FOR UPDATE", (site_id,))
        transform = data.transform
        if (
            set(transform) != {"west", "east", "south", "north"}
            or not all(isinstance(v, (float, int)) for v in transform.values())
            or not (
                -180 <= transform["west"] < transform["east"] <= 180
                and -90 <= transform["south"] < transform["north"] <= 90
            )
        ):
            raise HTTPException(422, "Задайте корректные координаты углов схемы")
        if data.media_id and not one(
            conn,
            "SELECT 1 FROM media_objects WHERE id=%s AND site_id=%s AND purpose='plan'",
            (data.media_id, site_id),
        ):
            raise HTTPException(403, "Чужая схема")
        version = one(
            conn,
            "SELECT coalesce(max(version),0)+1 AS n FROM site_map_versions WHERE site_id=%s",
            (site_id,),
        )["n"]
        map_id = one(
            conn,
            "INSERT INTO site_map_versions(site_id,version,media_id,transform) VALUES(%s,%s,%s,%s) RETURNING id",
            (site_id, version, data.media_id, Jsonb(transform)),
        )["id"]
        for zone in data.zones:
            poly = json.dumps(zone.polygon)
            valid = one(
                conn,
                "SELECT ST_IsValid(g) AND NOT ST_IsEmpty(g) AND ST_CoveredBy(g,boundary) AS ok FROM sites,CROSS JOIN LATERAL (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s),4326) AS g) q WHERE sites.id=%s".replace(
                    "sites,CROSS", "sites CROSS"
                ),
                (poly, site_id),
            )
            if not valid["ok"]:
                raise HTTPException(422, "Зона должна находиться внутри объекта")
            overlap = one(
                conn,
                "SELECT 1 FROM zone_shapes WHERE map_version_id=%s AND ST_Area(ST_Intersection(polygon,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)))>0",
                (map_id, poly),
            )
            if overlap:
                raise HTTPException(422, "Зоны не должны пересекаться")
            z = one(
                conn,
                "INSERT INTO zones(site_id,name) VALUES(%s,%s) ON CONFLICT(site_id,name) DO UPDATE SET name=excluded.name RETURNING id",
                (site_id, zone.name),
            )
            conn.execute(
                "INSERT INTO zone_shapes(zone_id,map_version_id,polygon) VALUES(%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))",
                (z["id"], map_id, poly),
            )
        audit(
            conn,
            actor,
            "map_published",
            site_id,
            version=version,
            zones=len(data.zones),
        )
        return {"id": map_id, "version": version}


@app.post("/api/v1/sites/{site_id}/plan")
async def upload_plan(site_id: UUID, actor: Auth, file: UploadFile = File(...)):
    content = await file.read(4_000_001)
    try:
        image = vision.decode_image(content)
        import cv2

        encoded = cv2.imencode(".png", image)[1].tobytes()
    except vision.VisionError as e:
        raise HTTPException(422, str(e))
    with transaction(actor) as conn:
        authorize_site(conn, actor, site_id, "manager")
        return {
            "media_id": storage.put_media(
                conn, encoded, "image/png", "plan", site_id=site_id
            )
        }


@app.get("/api/v1/media/{media_id}")
def media(media_id: UUID, actor: Auth):
    with transaction(actor) as conn:
        row = one(conn, "SELECT * FROM media_objects WHERE id=%s", (media_id,))
        if not row:
            raise HTTPException(404, "Файл недоступен")
        if row["purpose"] == "reference":
            if "admin" not in actor.roles:
                employee = one(
                    conn,
                    "SELECT principal_id FROM employees WHERE id=%s",
                    (row["employee_id"],),
                )
                own = employee and employee["principal_id"] == actor.id
                managed = one(
                    conn,
                    "SELECT 1 FROM employees e JOIN site_memberships m ON m.principal_id=e.principal_id WHERE e.id=%s AND m.active AND manager_site(m.site_id)",
                    (row["employee_id"],),
                )
                current = one(
                    conn,
                    "SELECT 1 FROM passage_attempts a JOIN gate_sessions g ON g.id=a.gate_session_id JOIN controller_assignments c ON c.principal_id=g.controller_id AND c.site_id=a.site_id AND c.revoked_at IS NULL WHERE a.employee_id=%s AND a.state='pending' AND a.deadline>now() AND g.controller_id=%s AND g.revoked_at IS NULL",
                    (row["employee_id"], actor.id),
                )
                if not (own or managed or current):
                    raise HTTPException(
                        403, "Эталон доступен только в рамках разрешённой проверки"
                    )
        else:
            authorize_staff_site(conn, actor, row["site_id"])
        content = storage.get_media(conn, row)
    return Response(
        content,
        media_type=row["content_type"],
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/api/v1/schedules")
def schedules(actor: Auth):
    with transaction(actor) as conn:
        if actor.roles == frozenset({"employee"}):
            employee = own_employee(conn, actor)
            return many(
                conn,
                "SELECT s.*,x.name AS site_name FROM scheduled_shifts s JOIN sites x ON x.id=s.site_id WHERE employee_id=%s AND ends_at>now()-interval '7 days' ORDER BY starts_at",
                (employee["id"],),
            )
        require_roles(actor, "admin", "manager")
        return many(
            conn,
            "SELECT s.*,e.full_name,x.name AS site_name FROM scheduled_shifts s JOIN sites x ON x.id=s.site_id JOIN employees e ON e.id=s.employee_id ORDER BY starts_at DESC LIMIT 100",
        )


@app.post("/api/v1/schedules")
def create_schedule(data: ScheduleInput, actor: Auth):
    if (
        data.starts_at.tzinfo is None
        or data.ends_at.tzinfo is None
        or data.ends_at <= data.starts_at
    ):
        raise HTTPException(422, "Некорректное время смены")
    with transaction(actor) as conn:
        authorize_site(conn, actor, data.site_id, "manager")
        if not one(
            conn,
            "SELECT 1 FROM employees e JOIN site_memberships m ON m.principal_id=e.principal_id WHERE e.id=%s AND m.site_id=%s AND m.kind='employee' AND m.active",
            (data.employee_id, data.site_id),
        ):
            raise HTTPException(403, "Сотрудник не назначен на объект")
        return one(
            conn,
            "INSERT INTO scheduled_shifts(employee_id,site_id,starts_at,ends_at) VALUES(%s,%s,%s,%s) RETURNING id",
            (data.employee_id, data.site_id, data.starts_at, data.ends_at),
        )


@app.get("/api/v1/settings/model")
def model_settings(actor: Auth):
    require_roles(actor, "admin", "manager")
    with transaction(actor) as conn:
        return one(conn, "SELECT provider,updated_at FROM model_settings")


@app.put("/api/v1/settings/model")
def set_model(data: ModelInput, actor: Auth):
    require_roles(actor, "admin")
    with transaction(actor) as conn:
        conn.execute(
            "UPDATE model_settings SET provider=%s,updated_at=now()", (data.provider,)
        )
        audit(conn, actor, "model_provider_changed", provider=data.provider)
    return {"provider": data.provider}


@app.post("/api/v1/assistant")
def assistant(data: AssistantInput, actor: Auth):
    period(data.start, data.end)
    with transaction(actor) as conn:
        authorize_site(conn, actor, data.site_id, "manager")
        provider = one(conn, "SELECT provider FROM model_settings")["provider"]
        request_id = one(
            conn,
            "INSERT INTO llm_requests(principal_id,site_id,provider,question) VALUES(%s,%s,%s,%s) RETURNING id",
            (actor.id, data.site_id, provider, data.question),
        )["id"]
    try:
        answer = llm.assistant(
            actor,
            data.site_id,
            data.question,
            data.start,
            data.end,
            data.employee_ids,
            provider,
        )
    except (httpx.HTTPError, ValueError, KeyError) as e:
        with transaction(actor) as conn:
            conn.execute(
                "UPDATE llm_requests SET error=%s WHERE id=%s",
                (type(e).__name__, request_id),
            )
        raise HTTPException(
            502,
            "Провайдер недоступен или вернул неподтверждённый ответ. Проверьте настройки и повторите запрос.",
        )
    with transaction(actor) as conn:
        authorize_site(conn, actor, data.site_id, "manager")
        conn.execute(
            "UPDATE llm_requests SET tools=%s,facts=%s,answer=%s WHERE id=%s",
            (
                Jsonb(answer["tools"]),
                Jsonb(answer["facts"]),
                Jsonb(jsonable_encoder(answer)),
                request_id,
            ),
        )
    return answer | {"request_id": request_id}


@app.get("/api/v1/push/key")
def push_key(actor: Auth):
    return {"public_key": secret("vapid_public")}


@app.post("/api/v1/push/subscriptions")
def subscribe(data: PushInput, actor: Auth):
    from urllib.parse import urlparse

    endpoint = data.subscription.get("endpoint", "")
    # Prevent the push worker from being used for SSRF to internal endpoints.
    host = urlparse(endpoint).hostname or ""
    allowed = (
        host == "fcm.googleapis.com"
        or host == "updates.push.services.mozilla.com"
        or host.endswith(".push.apple.com")
        or host == "web.push.apple.com"
        or host.endswith(".notify.windows.com")
    )
    if (
        not allowed
        or not endpoint.startswith("https://")
        or not data.subscription.get("keys")
    ):
        raise HTTPException(422, "Неизвестный push-сервис")
    with transaction(actor) as conn:
        return one(
            conn,
            "INSERT INTO push_subscriptions(principal_id,endpoint,subscription) VALUES(%s,%s,%s) ON CONFLICT(endpoint) DO UPDATE SET subscription=excluded.subscription WHERE push_subscriptions.principal_id=excluded.principal_id RETURNING id",
            (actor.id, endpoint, Jsonb(data.subscription)),
        )


@app.get("/api/v1/audit")
def audit_log(actor: Auth):
    require_roles(actor, "admin")
    with transaction(actor) as conn:
        return many(
            conn, "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT 200"
        )


@app.get("/api/v1/sites/{site_id}/report")
def export_report(site_id: UUID, actor: Auth, start: datetime, end: datetime):
    import csv
    import io

    period(start, end)
    with transaction(actor) as conn:
        authorize_site(conn, actor, site_id, "manager")
        rows = analytics.statistics(conn, site_id, start, end)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(
            ["Сотрудник", "Смен", "Часы", "Начало периода", "Конец периода"]
        )
        for row in rows:
            writer.writerow(
                [
                    row["full_name"],
                    row["shifts"],
                    round(row["hours"], 2),
                    start.isoformat(),
                    end.isoformat(),
                ]
            )
        content = stream.getvalue().encode("utf-8-sig")
        media_id = storage.put_media(
            conn, content, "text/csv; charset=utf-8", "report", site_id=site_id
        )
        audit(conn, actor, "report_exported", site_id, media_id=str(media_id))
        return {"media_id": media_id}
