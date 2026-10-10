import os
from pathlib import Path

SECRET_DIR = Path(os.getenv("SECRETS_DIR", "/runtime"))
MODEL_DIR = Path(os.getenv("MODELS_DIR", "/models"))
BOOTSTRAP_DIR = Path(os.getenv("BOOTSTRAP_DIR", "/bootstrap"))
ORIGIN = os.getenv("PUBLIC_ORIGIN", "https://localhost").rstrip("/")
ISSUER = ORIGIN + "/auth/realms/prohod"
OIDC_INTERNAL = os.getenv(
    "OIDC_INTERNAL_URL", "http://keycloak:8080/auth/realms/prohod"
)
FACE_THRESHOLD = float(os.getenv("FACE_THRESHOLD", "0.45"))
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://s3:8333")


def secret(name: str, directory: Path = SECRET_DIR) -> str:
    return (directory / name).read_text(encoding="utf-8").strip()


def dsn(role="app"):
    user = {
        "app": "prohod_app",
        "analytics": "prohod_analytics",
        "owner": "prohod_owner",
    }[role]
    password = secret("db_" + role, BOOTSTRAP_DIR if role == "owner" else SECRET_DIR)
    return f"host={os.getenv('DB_HOST', 'postgres')} port={os.getenv('DB_PORT', '5432')} dbname=prohod user={user} password={password}"
