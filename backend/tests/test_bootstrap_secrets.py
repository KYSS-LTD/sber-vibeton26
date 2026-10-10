from app import bootstrap


def test_init_exports_only_each_service_secrets_and_keeps_device_keys(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path / "bootstrap")
    monkeypatch.setattr(bootstrap, "SECRET_DIR", tmp_path / "runtime")
    expected = {
        "DB_SECRETS_DIR": {"db_owner", "db_app", "db_analytics", "db_keycloak"},
        "AUTH_SECRETS_DIR": {"db_keycloak", "kc_admin", "realm.json"},
        "S3_ADMIN_SECRETS_DIR": {"s3_admin_id", "s3_admin_secret", "s3_iam.json"},
        "MIGRATION_SECRETS_DIR": {"db_owner", "accounts.json"},
    }
    for variable in expected:
        monkeypatch.setenv(variable, str(tmp_path / variable))
    bootstrap.init_secrets()
    first = (tmp_path / "runtime/encryption_key").read_bytes()
    accounts = (tmp_path / "bootstrap/accounts.json").read_bytes()
    for variable, names in expected.items():
        folder = tmp_path / variable
        assert {p.name for p in folder.iterdir()} == names
        assert all(p.stat().st_mode & 0o004 for p in folder.iterdir())
    bootstrap.init_secrets()
    assert (tmp_path / "runtime/encryption_key").read_bytes() == first
    assert (tmp_path / "bootstrap/accounts.json").read_bytes() == accounts
