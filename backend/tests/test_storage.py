import os
import json
from uuid import uuid4
import pytest
from botocore.exceptions import ClientError
from app.db import transaction, SYSTEM, many
from app.storage import client, scoped_client
from app.security import decrypt

pytestmark = [
    pytest.mark.storage,
    pytest.mark.skipif(
        os.getenv("TEST_STORAGE") != "1",
        reason="Set TEST_STORAGE=1 for real S3 permission tests",
    ),
]


def test_real_s3_bucket_prefix_read_write_delete_and_anonymous_permissions():
    with transaction(SYSTEM) as conn:
        scopes = many(
            conn,
            "SELECT * FROM storage_scopes WHERE purpose='evidence_write' ORDER BY bucket LIMIT 2",
        )
        assert len(scopes) == 2
        bucket = scopes[0]["bucket"]
        key = "evidence/tests/" + str(uuid4())
        writer = client(json.loads(decrypt(scopes[0]["credentials_encrypted"])))
        writer.put_object(Bucket=bucket, Key=key, Body=b"test-permissions")
        reader = scoped_client(conn, bucket, "evidence_read")
        assert (
            reader.get_object(Bucket=bucket, Key=key)["Body"].read()
            == b"test-permissions"
        )
        for action in [
            lambda: writer.get_object(Bucket=bucket, Key=key),
            lambda: writer.delete_object(Bucket=bucket, Key=key),
            lambda: writer.put_bucket_policy(
                Bucket=bucket,
                Policy=json.dumps({"Version": "2012-10-17", "Statement": []}),
            ),
            lambda: writer.put_object(Bucket=bucket, Key="reports/forged", Body=b"x"),
            lambda: writer.put_object(Bucket=scopes[1]["bucket"], Key=key, Body=b"x"),
            lambda: reader.put_object(Bucket=bucket, Key=key, Body=b"x"),
        ]:
            with pytest.raises(ClientError):
                action()
        import boto3
        from botocore.config import Config
        from botocore import UNSIGNED
        from app.config import S3_ENDPOINT

        anonymous = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            config=Config(signature_version=UNSIGNED, s3={"addressing_style": "path"}),
        )
        with pytest.raises(ClientError):
            anonymous.get_object(Bucket=bucket, Key=key)
        with pytest.raises(ClientError):
            anonymous.put_object(Bucket=bucket, Key=key, Body=b"anonymous")
