import json
from uuid import uuid4
import boto3
from botocore.config import Config
from fastapi import HTTPException
from .config import S3_ENDPOINT
from .db import one
from .security import decrypt, digest


def client(credentials):
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id=credentials["access_key"],
        aws_secret_access_key=credentials["secret_key"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=15,
            retries={"max_attempts": 2},
        ),
    )


def scoped_client(conn, bucket, purpose):
    scope = one(
        conn,
        "SELECT * FROM storage_scopes WHERE bucket=%s AND purpose=%s",
        (bucket, purpose),
    )
    if not scope:
        raise HTTPException(503, "Права хранилища ещё не настроены")
    return client(json.loads(decrypt(scope["credentials_encrypted"])))


def put_media(conn, content, content_type, purpose, site_id=None, employee_id=None):
    media_id = uuid4()
    bucket = "biometrics" if purpose == "reference" else "site-" + str(site_id)
    prefix = {
        "reference": "references",
        "evidence": "evidence",
        "plan": "plans",
        "report": "reports",
    }[purpose]
    key = f"{prefix}/{employee_id or site_id}/{media_id}"
    scoped_client(conn, bucket, purpose + "_write").put_object(
        Bucket=bucket, Key=key, Body=content, ContentType=content_type
    )
    conn.execute(
        "INSERT INTO media_objects(id,site_id,employee_id,purpose,bucket,object_key,content_type,sha256,size_bytes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            media_id,
            site_id,
            employee_id,
            purpose,
            bucket,
            key,
            content_type,
            digest(content),
            len(content),
        ),
    )
    return media_id


def get_media(conn, row):
    result = scoped_client(conn, row["bucket"], row["purpose"] + "_read").get_object(
        Bucket=row["bucket"], Key=row["object_key"]
    )
    return result["Body"].read()
