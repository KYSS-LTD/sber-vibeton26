"""Idempotent initialisation. Storage administration never runs in the API container."""

import json
import os
from pathlib import Path
import secrets
import sys
import time
from uuid import uuid4, UUID
import boto3
import httpx
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from botocore.exceptions import ClientError
from .config import ORIGIN, ISSUER, S3_ENDPOINT, secret, BOOTSTRAP_DIR, SECRET_DIR
from .db import SYSTEM, transaction, one, many
from .security import encrypt
from .storage import client

ROOT = BOOTSTRAP_DIR


def init_secrets():
    ROOT.mkdir(parents=True, exist_ok=True)
    runtime = SECRET_DIR
    runtime.mkdir(parents=True, exist_ok=True)
    for name in [
        "db_owner",
        "db_app",
        "db_analytics",
        "db_keycloak",
        "kc_admin",
        "kc_service",
        "s3_admin_id",
        "s3_admin_secret",
    ]:
        path = ROOT / name
        if not path.exists():
            path.write_text(secrets.token_hex(24))
            path.chmod(0o600)
    for name in ["db_app", "db_analytics", "kc_service"]:
        (runtime / name).write_text((ROOT / name).read_text(encoding="utf-8"))
    if not (runtime / "encryption_key").exists():
        (runtime / "encryption_key").write_bytes(Fernet.generate_key())
    if not (runtime / "vapid_private").exists():
        key = ec.generate_private_key(ec.SECP256R1())
        (runtime / "vapid_private").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        import base64

        (runtime / "vapid_public").write_text(
            base64.urlsafe_b64encode(
                key.public_key().public_bytes(
                    serialization.Encoding.X962,
                    serialization.PublicFormat.UncompressedPoint,
                )
            )
            .decode()
            .rstrip("=")
        )
    accounts_path = ROOT / "accounts.json"
    if not accounts_path.exists():
        accounts = [
            {
                "id": str(uuid4()),
                "username": name,
                "password": secrets.token_urlsafe(16),
                "roles": [role],
            }
            for name, role in [
                ("admin", "admin"),
                ("manager", "manager"),
                ("guard-a", "controller"),
                ("guard-b", "controller"),
                ("worker-a", "employee"),
                ("worker-b", "employee"),
            ]
        ]
        accounts_path.write_text(
            json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        accounts_path.chmod(0o600)
    accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
    realm = {
        "realm": "prohod",
        "enabled": True,
        "displayName": "Проход · вход",
        "sslRequired": "external",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "accessTokenLifespan": 120,
        "roles": {
            "realm": [
                {"name": r} for r in ["admin", "manager", "controller", "employee"]
            ]
        },
        "clients": [
            {
                "clientId": "prohod-web",
                "publicClient": True,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "redirectUris": [ORIGIN + "/*"],
                "webOrigins": [ORIGIN],
                "attributes": {"pkce.code.challenge.method": "S256"},
                "protocolMappers": [
                    {
                        "name": "api-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.custom.audience": "prohod-api",
                            "access.token.claim": "true",
                        },
                    }
                ],
            },
            {
                "clientId": "prohod-management",
                "secret": secret("kc_service", ROOT),
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
            },
        ],
        "users": [
            {
                "id": a["id"],
                "username": a["username"],
                "enabled": True,
                "firstName": a["username"],
                "lastName": "Пользователь",
                "email": a["username"] + "@prohod.local",
                "emailVerified": True,
                "realmRoles": a["roles"],
                "credentials": [
                    {"type": "password", "value": a["password"], "temporary": False}
                ],
            }
            for a in accounts
        ]
        + [
            {
                "username": "service-account-prohod-management",
                "enabled": True,
                "serviceAccountClientId": "prohod-management",
                "clientRoles": {
                    "realm-management": ["manage-users", "view-users", "view-realm"]
                },
            }
        ],
    }
    (ROOT / "realm.json").write_text(
        json.dumps(realm, ensure_ascii=False), encoding="utf-8"
    )
    iam_file = ROOT / "s3_iam.json"
    iam_config = (
        json.loads(iam_file.read_text(encoding="utf-8")) if iam_file.exists() else {}
    )
    if "sts" not in iam_config:
        import base64

        iam_file.write_text(
            json.dumps(
                {
                    "sts": {
                        "signingKey": base64.b64encode(secrets.token_bytes(32)).decode()
                    }
                }
            )
        )
    # Authorisation is the mounted volume; files must also be readable by the
    # PostgreSQL/Keycloak non-root users inside their individual containers.
    exports = {
        "DB_SECRETS_DIR": ["db_owner", "db_app", "db_analytics", "db_keycloak"],
        "AUTH_SECRETS_DIR": ["db_keycloak", "kc_admin", "realm.json"],
        "S3_ADMIN_SECRETS_DIR": ["s3_admin_id", "s3_admin_secret", "s3_iam.json"],
        "MIGRATION_SECRETS_DIR": ["db_owner", "accounts.json"],
    }
    for variable, names in exports.items():
        destination = os.getenv(variable)
        if destination:
            folder = Path(destination)
            folder.mkdir(parents=True, exist_ok=True)
            for name in names:
                target = folder / name
                target.write_bytes((ROOT / name).read_bytes())
                target.chmod(0o644)


def migrate():
    with transaction(SYSTEM, owner=True) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(810010)")
        exists = one(conn, "SELECT to_regclass('public.schema_migrations') AS value")[
            "value"
        ]
        applied = (
            set()
            if not exists
            else {
                row["version"]
                for row in many(conn, "SELECT version FROM schema_migrations")
            }
        )
        for migration in sorted(
            (Path(__file__).parent.parent / "sql").glob("[0-9][0-9][0-9]_*.sql")
        ):
            version = int(migration.name.split("_", 1)[0])
            if version not in applied:
                conn.execute(migration.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES(%s) ON CONFLICT DO NOTHING",
                    (version,),
                )
        conn.execute(
            "INSERT INTO principals(id,issuer,subject,username,roles) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            (SYSTEM.id, ISSUER, "system", "system", ["admin"]),
        )
        accounts = json.loads((ROOT / "accounts.json").read_text(encoding="utf-8"))
        for account in accounts:
            conn.execute(
                "INSERT INTO principals(id,issuer,subject,username,roles) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET issuer=excluded.issuer",
                (
                    account["id"],
                    ISSUER,
                    account["id"],
                    account["username"],
                    account["roles"],
                ),
            )
        site_ids = [
            UUID("10000000-0000-0000-0000-000000000001"),
            UUID("10000000-0000-0000-0000-000000000002"),
        ]
        for i, site_id in enumerate(site_ids):
            lng = 37.615 + i * 0.02
            lat = 55.750
            polygon = {
                "type": "Polygon",
                "coordinates": [
                    [
                        [lng, lat],
                        [lng + 0.005, lat],
                        [lng + 0.005, lat + 0.003],
                        [lng, lat + 0.003],
                        [lng, lat],
                    ]
                ],
            }
            conn.execute(
                "INSERT INTO sites(id,name,boundary) VALUES(%s,%s,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)) ON CONFLICT(id) DO NOTHING",
                (site_id, f"Объект {chr(65 + i)}", json.dumps(polygon)),
            )
            checkpoint_id = UUID(f"20000000-0000-0000-0000-{i + 1:012d}")
            conn.execute(
                "INSERT INTO checkpoints(id,site_id,name) VALUES(%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                (checkpoint_id, site_id, "Главный КПП"),
            )
            manager = next(a for a in accounts if a["username"] == "manager")
            conn.execute(
                "INSERT INTO site_memberships(principal_id,site_id,kind) VALUES(%s,%s,'manager') ON CONFLICT DO NOTHING",
                (manager["id"], site_id),
            )
            guard = next(a for a in accounts if a["username"] == f"guard-{chr(97 + i)}")
            if not one(
                conn,
                "SELECT 1 FROM controller_assignments WHERE principal_id=%s",
                (guard["id"],),
            ):
                conn.execute(
                    "INSERT INTO controller_assignments(principal_id,site_id) VALUES(%s,%s)",
                    (guard["id"], site_id),
                )
            worker = next(
                a for a in accounts if a["username"] == f"worker-{chr(97 + i)}"
            )
            conn.execute(
                "INSERT INTO employees(principal_id,full_name,personnel_number) VALUES(%s,%s,%s) ON CONFLICT(principal_id) DO NOTHING",
                (worker["id"], ["Алексей Смирнов", "Иван Петров"][i], f"DEMO-{i + 1}"),
            )
            conn.execute(
                "INSERT INTO site_memberships(principal_id,site_id,kind) VALUES(%s,%s,'employee') ON CONFLICT DO NOTHING",
                (worker["id"], site_id),
            )


def provision_bucket(conn, bucket, site_id, purposes):
    admin = {
        "access_key": secret("s3_admin_id", ROOT),
        "secret_key": secret("s3_admin_secret", ROOT),
    }
    s3 = client(admin)
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] not in [
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        ]:
            raise
    # Seaweed's basic IAM translation accepts Allow only and maps PutObject to Write.
    # Explicit bucket policy supplies the delete prohibition for that broader action.
    s3.put_bucket_policy(
        Bucket=bucket,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": ["s3:DeleteObject", "s3:DeleteBucket"],
                        "Resource": [
                            f"arn:aws:s3:::{bucket}",
                            f"arn:aws:s3:::{bucket}/*",
                        ],
                    }
                ],
            }
        ),
    )
    iam = boto3.client(
        "iam",
        endpoint_url=S3_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id=admin["access_key"],
        aws_secret_access_key=admin["secret_key"],
    )
    for purpose, prefix in purposes.items():
        for verb, action in [("read", "s3:GetObject"), ("write", "s3:PutObject")]:
            name = bucket + "-" + purpose + "-" + verb
            if one(
                conn,
                "SELECT 1 FROM storage_scopes WHERE bucket=%s AND purpose=%s",
                (bucket, purpose + "_" + verb),
            ):
                continue
            try:
                iam.create_user(UserName=name)
            except ClientError as e:
                if e.response["Error"]["Code"] != "EntityAlreadyExists":
                    raise
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": [action],
                        "Resource": [f"arn:aws:s3:::{bucket}/{prefix}/*"],
                    }
                ],
            }
            iam.put_user_policy(
                UserName=name, PolicyName="scope", PolicyDocument=json.dumps(policy)
            )
            key = iam.create_access_key(UserName=name)["AccessKey"]
            conn.execute(
                "INSERT INTO storage_scopes(site_id,purpose,bucket,prefix,credentials_encrypted) VALUES(%s,%s,%s,%s,%s)",
                (
                    site_id,
                    purpose + "_" + verb,
                    bucket,
                    prefix,
                    encrypt(
                        json.dumps(
                            {
                                "access_key": key["AccessKeyId"],
                                "secret_key": key["SecretAccessKey"],
                            }
                        )
                    ),
                ),
            )


def provision():
    for _ in range(120):
        try:
            client(
                {
                    "access_key": secret("s3_admin_id", ROOT),
                    "secret_key": secret("s3_admin_secret", ROOT),
                }
            ).list_buckets()
            break
        except Exception:
            time.sleep(2)
    else:
        raise RuntimeError("S3 did not become ready")
    filer = os.getenv("S3_FILER_URL", "http://s3:8888")
    existing = httpx.get(filer + "/etc/seaweedfs/iam/identity.json", timeout=10)
    if existing.status_code == 404:
        initial = {
            "identities": [
                {
                    "name": "bootstrap-admin",
                    "credentials": [
                        {
                            "accessKey": secret("s3_admin_id", ROOT),
                            "secretKey": secret("s3_admin_secret", ROOT),
                        }
                    ],
                    "actions": ["Admin", "Read", "Write"],
                }
            ]
        }
        response = httpx.post(
            filer + "/etc/seaweedfs/iam/",
            files={
                "file": (
                    "identity.json",
                    json.dumps(initial).encode(),
                    "application/json",
                )
            },
            timeout=10,
        )
        response.raise_for_status()
        time.sleep(1)
    elif existing.status_code != 200:
        existing.raise_for_status()
    with transaction(SYSTEM) as conn:
        provision_bucket(conn, "biometrics", None, {"reference": "references"})
        for site in many(conn, "SELECT * FROM sites WHERE NOT active"):
            provision_bucket(
                conn,
                "site-" + str(site["id"]),
                site["id"],
                {"plan": "plans", "evidence": "evidence", "report": "reports"},
            )
            conn.execute("UPDATE sites SET active=true WHERE id=%s", (site["id"],))


def storage_worker():
    while True:
        try:
            provision()
            with transaction(SYSTEM) as conn:
                conn.execute(
                    "UPDATE jobs SET state='done',updated_at=now() WHERE kind='provision_site' AND state='pending'"
                )
        except Exception as e:
            print("Storage provisioning retry:", type(e).__name__, flush=True)
        time.sleep(5)


def ollama():
    url = os.getenv("OLLAMA_URL", "http://ollama:11434")
    pinned = json.loads(
        (Path(__file__).parent.parent / "llm-model.json").read_text(encoding="utf-8")
    )
    for _ in range(120):
        try:
            with httpx.Client(timeout=1800) as c:
                result = c.post(
                    url + "/api/pull", json={"model": pinned["name"], "stream": False}
                )
                result.raise_for_status()
                tags = c.get(url + "/api/tags")
                tags.raise_for_status()
                model = next(
                    m for m in tags.json()["models"] if m["name"] == pinned["name"]
                )
                if model["digest"].removeprefix("sha256:") != pinned["digest"]:
                    raise RuntimeError(
                        "Ollama model digest changed; review llm-model.json before updating"
                    )
            # Warm the same constrained inference path used by the assistant.
            # Initial CUDA compilation must finish during setup, before users ask.
            from .llm import gateway, PROMPTS, TOOLS

            gateway.complete(
                "ollama",
                (PROMPTS / "select.txt").read_text(encoding="utf-8"),
                {"question": "Сколько сотрудников сейчас на объекте?"},
                TOOLS["selection_schema"],
            )
            return
        except httpx.HTTPError:
            time.sleep(2)
    raise RuntimeError("Ollama model initialization failed")


if __name__ == "__main__":
    {
        "secrets": init_secrets,
        "migrate": migrate,
        "provision": provision,
        "storage-worker": storage_worker,
        "ollama": ollama,
    }[sys.argv[1]]()
