CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE principals (
 id uuid PRIMARY KEY, issuer text NOT NULL, subject text NOT NULL, username text NOT NULL,
 roles text[] NOT NULL DEFAULT '{}', active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(issuer,subject)
);
CREATE TABLE employees (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), principal_id uuid NOT NULL UNIQUE REFERENCES principals,
 full_name text NOT NULL, personnel_number text NOT NULL UNIQUE, active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sites (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL, timezone text NOT NULL DEFAULT 'Europe/Moscow',
 boundary geometry(Polygon,4326) NOT NULL, active boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), CHECK(ST_IsValid(boundary))
);
CREATE TABLE site_memberships (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), principal_id uuid NOT NULL REFERENCES principals,
 site_id uuid NOT NULL REFERENCES sites, kind text NOT NULL CHECK(kind IN ('employee','manager')),
 active boolean NOT NULL DEFAULT true, UNIQUE(principal_id,site_id,kind)
);
CREATE TABLE controller_assignments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), principal_id uuid NOT NULL REFERENCES principals,
 site_id uuid NOT NULL REFERENCES sites, assigned_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz
);
CREATE UNIQUE INDEX one_controller_site ON controller_assignments(principal_id) WHERE revoked_at IS NULL;
CREATE TABLE checkpoints (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), site_id uuid NOT NULL REFERENCES sites,
 name text NOT NULL, location geometry(Point,4326), active boolean NOT NULL DEFAULT true
);
CREATE TABLE storage_scopes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), site_id uuid REFERENCES sites,
 purpose text NOT NULL, bucket text NOT NULL, prefix text NOT NULL,
 credentials_encrypted text NOT NULL, UNIQUE(bucket,purpose)
);
CREATE TABLE media_objects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), site_id uuid REFERENCES sites, employee_id uuid REFERENCES employees,
 purpose text NOT NULL CHECK(purpose IN ('reference','evidence','plan','report')),
 bucket text NOT NULL, object_key text NOT NULL UNIQUE, content_type text NOT NULL, sha256 text NOT NULL,
 size_bytes integer NOT NULL CHECK(size_bytes>0), created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((purpose='reference' AND employee_id IS NOT NULL) OR (purpose<>'reference' AND site_id IS NOT NULL))
);
CREATE TABLE face_reference_sets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), employee_id uuid NOT NULL REFERENCES employees,
 version integer NOT NULL, state text NOT NULL CHECK(state IN ('draft','active','retired')),
 model_version text NOT NULL, published_at timestamptz, UNIQUE(employee_id,version)
);
CREATE UNIQUE INDEX one_reference_set ON face_reference_sets(employee_id) WHERE state='active';
CREATE TABLE face_reference_photos (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), set_id uuid NOT NULL REFERENCES face_reference_sets,
 slot integer NOT NULL CHECK(slot BETWEEN 1 AND 3), media_id uuid NOT NULL UNIQUE REFERENCES media_objects,
 quality jsonb NOT NULL, image_sha256 text NOT NULL, UNIQUE(set_id,slot), UNIQUE(set_id,image_sha256)
);
CREATE TABLE face_templates (
 photo_id uuid PRIMARY KEY REFERENCES face_reference_photos, embedding real[] NOT NULL,
 model_version text NOT NULL, CHECK(array_length(embedding,1)=128)
);
CREATE FUNCTION reference_count_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
 IF NEW.state='active' THEN
  SELECT count(*) INTO n FROM face_reference_photos p JOIN face_templates t ON t.photo_id=p.id WHERE p.set_id=NEW.id;
  IF n<>3 THEN RAISE EXCEPTION 'An active reference set must contain exactly three templates'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER three_references AFTER INSERT OR UPDATE ON face_reference_sets
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reference_count_guard();
CREATE FUNCTION frozen_reference_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s text;
BEGIN
 SELECT state INTO s FROM face_reference_sets WHERE id=COALESCE(NEW.set_id,OLD.set_id);
 IF s<>'draft' THEN RAISE EXCEPTION 'Published reference photos are immutable'; END IF;
 RETURN COALESCE(NEW,OLD);
END $$;
CREATE TRIGGER immutable_reference BEFORE UPDATE OR DELETE ON face_reference_photos
 FOR EACH ROW EXECUTE FUNCTION frozen_reference_guard();
CREATE FUNCTION frozen_template_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s text;
BEGIN
 SELECT r.state INTO s FROM face_reference_sets r JOIN face_reference_photos p ON p.set_id=r.id
 WHERE p.id=OLD.photo_id;
 IF s<>'draft' THEN RAISE EXCEPTION 'Published templates are immutable'; END IF;
 RETURN COALESCE(NEW,OLD);
END $$;
CREATE TRIGGER immutable_template BEFORE UPDATE OR DELETE ON face_templates
 FOR EACH ROW EXECUTE FUNCTION frozen_template_guard();
CREATE TABLE employee_device_keys (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), employee_id uuid NOT NULL REFERENCES employees,
 public_jwk jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz
);
CREATE UNIQUE INDEX one_active_device ON employee_device_keys(employee_id) WHERE revoked_at IS NULL;
CREATE TABLE daily_qr_credentials (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), employee_id uuid NOT NULL REFERENCES employees,
 site_id uuid NOT NULL REFERENCES sites, local_date date NOT NULL,
 token_hash text NOT NULL UNIQUE, token_encrypted text NOT NULL, expires_at timestamptz NOT NULL,
 revoked_at timestamptz, UNIQUE(employee_id,site_id,local_date,token_hash)
);
CREATE UNIQUE INDEX active_daily_qr ON daily_qr_credentials(employee_id,site_id,local_date) WHERE revoked_at IS NULL;
CREATE TABLE gate_sessions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), controller_id uuid NOT NULL REFERENCES principals,
 checkpoint_id uuid NOT NULL REFERENCES checkpoints, site_id uuid NOT NULL REFERENCES sites,
 created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz
);
CREATE TABLE passage_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), site_id uuid NOT NULL REFERENCES sites,
 employee_id uuid NOT NULL REFERENCES employees, qr_id uuid NOT NULL REFERENCES daily_qr_credentials,
 gate_session_id uuid NOT NULL REFERENCES gate_sessions, reference_set_id uuid NOT NULL REFERENCES face_reference_sets,
 device_key_id uuid NOT NULL REFERENCES employee_device_keys,
 direction text NOT NULL CHECK(direction IN ('entry','exit')), nonce text NOT NULL UNIQUE,
 challenge text NOT NULL, actions text[] NOT NULL, deadline timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','allowed','denied','expired','cancelled')),
 reason text, vision_result jsonb, evidence_media_id uuid REFERENCES media_objects,
 shift_id uuid, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE UNIQUE INDEX one_pending_employee ON passage_attempts(employee_id) WHERE state='pending';
CREATE UNIQUE INDEX one_pending_gate ON passage_attempts(gate_session_id) WHERE state='pending';
CREATE TABLE device_confirmations (
 attempt_id uuid PRIMARY KEY REFERENCES passage_attempts, employee_id uuid NOT NULL REFERENCES employees,
 device_key_id uuid NOT NULL REFERENCES employee_device_keys, signature text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE scheduled_shifts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), employee_id uuid NOT NULL REFERENCES employees,
 site_id uuid NOT NULL REFERENCES sites, starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL,
 notified_at timestamptz, CHECK(ends_at>starts_at)
);
CREATE TABLE shifts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), employee_id uuid NOT NULL REFERENCES employees,
 site_id uuid NOT NULL REFERENCES sites, opened_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz,
 entry_attempt_id uuid NOT NULL UNIQUE REFERENCES passage_attempts, exit_attempt_id uuid UNIQUE REFERENCES passage_attempts,
 CHECK(closed_at IS NULL OR closed_at>=opened_at)
);
ALTER TABLE passage_attempts ADD CONSTRAINT attempt_shift FOREIGN KEY(shift_id) REFERENCES shifts;
CREATE UNIQUE INDEX one_open_shift ON shifts(employee_id) WHERE closed_at IS NULL;
CREATE TABLE site_map_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), site_id uuid NOT NULL REFERENCES sites,
 version integer NOT NULL, media_id uuid REFERENCES media_objects, transform jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(site_id,version)
);
CREATE TABLE zones (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), site_id uuid NOT NULL REFERENCES sites, name text NOT NULL, UNIQUE(site_id,name)
);
CREATE TABLE zone_shapes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), zone_id uuid NOT NULL REFERENCES zones,
 map_version_id uuid NOT NULL REFERENCES site_map_versions, polygon geometry(Polygon,4326) NOT NULL,
 UNIQUE(zone_id,map_version_id), CHECK(ST_IsValid(polygon))
);
CREATE TABLE location_checks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), shift_id uuid NOT NULL REFERENCES shifts,
 employee_id uuid NOT NULL REFERENCES employees, site_id uuid NOT NULL REFERENCES sites,
 sample_id uuid NOT NULL, measured_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
 position geometry(Point,4326) NOT NULL, accuracy_m real NOT NULL CHECK(accuracy_m BETWEEN 0 AND 1000),
 inside boolean NOT NULL, zone_shape_id uuid REFERENCES zone_shapes,
 suspicious boolean NOT NULL DEFAULT false, UNIQUE(shift_id,sample_id)
);
CREATE INDEX location_shift_time ON location_checks(shift_id,received_at);
CREATE TABLE alerts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), site_id uuid NOT NULL REFERENCES sites,
 employee_id uuid NOT NULL REFERENCES employees, shift_id uuid NOT NULL REFERENCES shifts,
 kind text NOT NULL, detail text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), acknowledged_at timestamptz
);
CREATE TABLE notifications (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), principal_id uuid NOT NULL REFERENCES principals,
 site_id uuid REFERENCES sites, kind text NOT NULL, payload jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), read_at timestamptz, pushed_at timestamptz
);
CREATE TABLE push_subscriptions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), principal_id uuid NOT NULL REFERENCES principals,
 endpoint text NOT NULL UNIQUE, subscription jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE model_settings (
 id boolean PRIMARY KEY DEFAULT true CHECK(id), provider text NOT NULL CHECK(provider IN ('ollama','gigachat')),
 updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO model_settings(id,provider) VALUES(true,'ollama');
CREATE TABLE llm_requests (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), principal_id uuid NOT NULL REFERENCES principals,
 site_id uuid NOT NULL REFERENCES sites, provider text NOT NULL, question text NOT NULL,
 tools jsonb NOT NULL DEFAULT '[]', facts jsonb, answer jsonb, error text,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE audit_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), principal_id uuid REFERENCES principals,
 site_id uuid REFERENCES sites, employee_id uuid REFERENCES employees, action text NOT NULL,
 detail jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), kind text NOT NULL, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','done','failed')),
 error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE checkpoints ADD UNIQUE(id,site_id);
ALTER TABLE gate_sessions ADD UNIQUE(id,site_id);
ALTER TABLE gate_sessions ADD FOREIGN KEY(checkpoint_id,site_id) REFERENCES checkpoints(id,site_id);
ALTER TABLE daily_qr_credentials ADD UNIQUE(id,employee_id,site_id);
ALTER TABLE face_reference_sets ADD UNIQUE(id,employee_id);
ALTER TABLE employee_device_keys ADD UNIQUE(id,employee_id);
ALTER TABLE passage_attempts ADD FOREIGN KEY(qr_id,employee_id,site_id) REFERENCES daily_qr_credentials(id,employee_id,site_id);
ALTER TABLE passage_attempts ADD FOREIGN KEY(gate_session_id,site_id) REFERENCES gate_sessions(id,site_id);
ALTER TABLE passage_attempts ADD FOREIGN KEY(reference_set_id,employee_id) REFERENCES face_reference_sets(id,employee_id);
ALTER TABLE passage_attempts ADD FOREIGN KEY(device_key_id,employee_id) REFERENCES employee_device_keys(id,employee_id);
ALTER TABLE shifts ADD UNIQUE(id,employee_id,site_id);
ALTER TABLE location_checks ADD FOREIGN KEY(shift_id,employee_id,site_id) REFERENCES shifts(id,employee_id,site_id);
CREATE FUNCTION actor() RETURNS uuid LANGUAGE sql STABLE AS $$
 SELECT nullif(current_setting('app.actor',true),'')::uuid
$$;
CREATE FUNCTION app_admin() RETURNS boolean LANGUAGE sql STABLE AS $$
 SELECT COALESCE(current_setting('app.roles',true),'') LIKE '%|admin|%'
$$;
CREATE FUNCTION scoped_site(s uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public AS $$
 SELECT app_admin() OR EXISTS(SELECT 1 FROM site_memberships WHERE site_id=s AND principal_id=actor() AND active)
 OR EXISTS(SELECT 1 FROM controller_assignments WHERE site_id=s AND principal_id=actor() AND revoked_at IS NULL)
$$;
CREATE FUNCTION scoped_employee(e uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public AS $$
 SELECT app_admin() OR EXISTS(SELECT 1 FROM employees WHERE id=e AND principal_id=actor())
 OR EXISTS(SELECT 1 FROM employees x JOIN site_memberships m ON m.principal_id=x.principal_id
 WHERE x.id=e AND m.active AND scoped_site(m.site_id))
$$;
CREATE FUNCTION scoped_principal(p uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public AS $$
 SELECT app_admin() OR p=actor() OR EXISTS(SELECT 1 FROM site_memberships m WHERE m.principal_id=p AND m.active AND scoped_site(m.site_id))
$$;
CREATE FUNCTION manager_site(s uuid) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public AS $$
 SELECT app_admin() OR EXISTS(SELECT 1 FROM site_memberships WHERE site_id=s AND principal_id=actor() AND kind='manager' AND active)
$$;
DO $$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['principals','employees','sites','site_memberships','controller_assignments','checkpoints',
 'storage_scopes','media_objects','face_reference_sets','face_reference_photos','face_templates','employee_device_keys',
 'daily_qr_credentials','gate_sessions','passage_attempts','device_confirmations','scheduled_shifts','shifts',
 'site_map_versions','zones','zone_shapes','location_checks','alerts','notifications','push_subscriptions','model_settings',
 'llm_requests','audit_events','jobs'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 END LOOP;
END $$;
CREATE POLICY p ON principals USING(scoped_principal(id));
CREATE POLICY p ON employees USING(scoped_employee(id));
CREATE POLICY p ON sites USING(scoped_site(id));
CREATE POLICY p ON site_memberships USING(scoped_site(site_id));
CREATE POLICY p ON controller_assignments USING(app_admin() OR principal_id=actor());
CREATE POLICY p ON checkpoints USING(scoped_site(site_id));
CREATE POLICY p ON storage_scopes USING(app_admin() OR (site_id IS NOT NULL AND scoped_site(site_id)) OR bucket='biometrics');
CREATE POLICY p ON media_objects USING((site_id IS NOT NULL AND scoped_site(site_id)) OR (employee_id IS NOT NULL AND scoped_employee(employee_id)));
CREATE POLICY p ON face_reference_sets USING(scoped_employee(employee_id));
CREATE POLICY p ON face_reference_photos USING(EXISTS(SELECT 1 FROM face_reference_sets r WHERE r.id=set_id));
CREATE POLICY p ON face_templates USING(EXISTS(SELECT 1 FROM face_reference_photos p WHERE p.id=photo_id));
CREATE POLICY p ON employee_device_keys USING(scoped_employee(employee_id));
CREATE POLICY p ON daily_qr_credentials USING(scoped_employee(employee_id) AND scoped_site(site_id));
CREATE POLICY p ON gate_sessions USING(app_admin() OR controller_id=actor());
CREATE POLICY p ON passage_attempts USING(scoped_site(site_id) AND scoped_employee(employee_id));
CREATE POLICY p ON device_confirmations USING(scoped_employee(employee_id));
CREATE POLICY p ON scheduled_shifts USING(scoped_employee(employee_id) AND scoped_site(site_id));
CREATE POLICY p ON shifts USING(scoped_employee(employee_id) AND scoped_site(site_id));
CREATE POLICY p ON site_map_versions USING(scoped_site(site_id));
CREATE POLICY p ON zones USING(scoped_site(site_id));
CREATE POLICY p ON zone_shapes USING(EXISTS(SELECT 1 FROM zones z WHERE z.id=zone_id));
CREATE POLICY p ON location_checks USING(scoped_employee(employee_id) AND scoped_site(site_id));
CREATE POLICY p ON alerts USING(scoped_employee(employee_id) AND scoped_site(site_id));
CREATE POLICY p ON notifications USING(app_admin() OR principal_id=actor());
CREATE POLICY p ON push_subscriptions USING(app_admin() OR principal_id=actor());
CREATE POLICY p ON model_settings USING(true) WITH CHECK(app_admin());
CREATE POLICY p ON llm_requests USING(app_admin() OR principal_id=actor());
CREATE POLICY p ON audit_events USING(app_admin() OR (site_id IS NOT NULL AND manager_site(site_id)) OR principal_id=actor());
CREATE POLICY p ON jobs USING(app_admin());
CREATE FUNCTION queue_alert_notifications(aid uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE a alerts; recipients jsonb;
BEGIN
 SELECT * INTO a FROM alerts WHERE id=aid;
 IF a.id IS NULL OR NOT scoped_employee(a.employee_id) OR NOT scoped_site(a.site_id) THEN
  RAISE EXCEPTION 'Alert not available';
 END IF;
 SELECT jsonb_agg(p) INTO recipients FROM (
  SELECT principal_id::text AS p FROM employees WHERE id=a.employee_id
  UNION SELECT principal_id::text FROM site_memberships WHERE site_id=a.site_id AND kind='manager' AND active
 ) q;
 INSERT INTO jobs(kind,payload) VALUES('notify_alert',jsonb_build_object('principal_ids',recipients,
  'site_id',a.site_id::text,'alert_id',a.id::text,'detail',a.detail));
END $$;
CREATE FUNCTION queue_shift_notification(sid uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
DECLARE s shifts; p uuid; permitted boolean;
BEGIN
 SELECT * INTO s FROM shifts WHERE id=sid;
 SELECT EXISTS(SELECT 1 FROM passage_attempts a JOIN gate_sessions g ON g.id=a.gate_session_id
  JOIN controller_assignments c ON c.principal_id=g.controller_id AND c.site_id=s.site_id AND c.revoked_at IS NULL
  WHERE a.id=CASE WHEN s.closed_at IS NULL THEN s.entry_attempt_id ELSE s.exit_attempt_id END
   AND g.controller_id=actor() AND g.revoked_at IS NULL) INTO permitted;
 IF NOT app_admin() AND NOT permitted THEN RAISE EXCEPTION 'Shift notification denied'; END IF;
 SELECT principal_id INTO p FROM employees WHERE id=s.employee_id;
 INSERT INTO notifications(principal_id,site_id,kind,payload) VALUES(p,s.site_id,
  CASE WHEN s.closed_at IS NULL THEN 'shift_opened' ELSE 'shift_closed' END,jsonb_build_object('shift_id',sid::text));
END $$;
GRANT USAGE ON SCHEMA public TO prohod_app,prohod_analytics;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO prohod_app;
GRANT SELECT ON sites,principals,employees,site_memberships,controller_assignments,checkpoints,shifts,location_checks,
 zones,zone_shapes,site_map_versions,passage_attempts,alerts TO prohod_analytics;
REVOKE ALL ON schema_migrations FROM prohod_app;
REVOKE UPDATE ON audit_events FROM prohod_app;
ALTER ROLE prohod_analytics SET default_transaction_read_only=on;
ALTER ROLE prohod_analytics SET statement_timeout='5s';
INSERT INTO schema_migrations(version) VALUES(1);
