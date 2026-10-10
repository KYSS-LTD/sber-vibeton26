"""Acceptance helper; use ONLY on an isolated restored Compose project."""
import hashlib, json, pathlib, sys, os
from uuid import uuid4
sys.path.insert(0,'/app/tests')
sys.path.insert(0,'/app')
from app.db import SYSTEM, transaction, one, many
from app.storage import scoped_client
if os.getenv('ALLOW_ACCEPTANCE_FIXTURES') != 'isolated-restore':
    raise SystemExit('Use only an isolated restore project and set ALLOW_ACCEPTANCE_FIXTURES=isolated-restore')
state=pathlib.Path('/acceptance/persistence-state.json')
if sys.argv[1]=='seed':
    from pytest import MonkeyPatch
    import test_integration
    from app import passages
    fixture=test_integration.fixture_db.__wrapped__(MonkeyPatch())
    conn, ids, actors, context=next(fixture)
    context(actors['guard'])
    attempt=passages.start_attempt(conn,actors['guard'],ids['session'],ids['qr_token'],'entry')
    conn.execute('INSERT INTO device_confirmations(attempt_id,employee_id,device_key_id,signature) VALUES(%s,%s,%s,%s)',(attempt['id'],ids['employee'],ids['key'],'test'))
    shift=passages.finalize(conn,actors['guard'],attempt['id'],{'passed':True})['shift_id']
    conn.commit()
    fixture.close()
    with transaction(SYSTEM) as conn:
        scope=one(conn,"SELECT bucket FROM storage_scopes WHERE purpose='evidence_write' ORDER BY bucket LIMIT 1")
        bucket=scope['bucket']; key='evidence/tests/persistence-'+str(uuid4()); body=b'acceptance-persistence-object'
        scoped_client(conn,bucket,'evidence_write').put_object(Bucket=bucket,Key=key,Body=body)
        row=one(conn,'SELECT id,opened_at,closed_at FROM shifts WHERE id=%s',(shift,))
        settings=many(conn,'SELECT * FROM model_settings ORDER BY id')
    state.write_text(json.dumps({'shift':str(shift),'employee':str(ids['employee']),'opened_at':str(row['opened_at']),'bucket':bucket,'key':key,'hash':hashlib.sha256(body).hexdigest(),'settings':settings},default=str))
    state.chmod(0o600)
    print('Seeded isolated TEST employee, open shift, S3 evidence and model settings baseline')
else:
    expected=json.loads(state.read_text())
    with transaction(SYSTEM) as conn:
        row=one(conn,'SELECT opened_at,closed_at FROM shifts WHERE id=%s AND employee_id=%s',(expected['shift'],expected['employee']))
        assert row and row['closed_at'] is None and str(row['opened_at'])==expected['opened_at']
        settings=many(conn,'SELECT * FROM model_settings ORDER BY id')
        assert json.loads(json.dumps(settings,default=str))==expected['settings']
        body=scoped_client(conn,expected['bucket'],'evidence_read').get_object(Bucket=expected['bucket'],Key=expected['key'])['Body'].read()
        assert hashlib.sha256(body).hexdigest()==expected['hash']
    print('PASS: open shift, S3 object hash and model settings survived')
