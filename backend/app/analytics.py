from .db import many, one


def heatmap(conn, site_id, start, end, employee_ids=None):
    params = {"site": site_id, "start": start, "end": end, "employees": employee_ids}
    intervals = """
      WITH ordered AS (
       SELECT l.*,s.opened_at,s.closed_at,
        lead(l.received_at) OVER(PARTITION BY l.shift_id ORDER BY l.received_at) AS next_at
       FROM location_checks l JOIN shifts s ON s.id=l.shift_id
       WHERE l.site_id=%(site)s AND l.received_at >= %(start)s::timestamptz-interval '10 minutes'
        AND l.received_at < %(end)s AND (%(employees)s::uuid[] IS NULL OR l.employee_id=ANY(%(employees)s::uuid[]))
      ), weighted AS (
       SELECT ordered.*,zs.zone_id,greatest(0,extract(epoch FROM
        least(coalesce(next_at,received_at+interval '10 minutes'),received_at+interval '10 minutes',
         coalesce(closed_at,%(end)s::timestamptz),%(end)s::timestamptz,now())
         - greatest(received_at,opened_at,%(start)s::timestamptz)))/60 AS minutes
       FROM ordered LEFT JOIN zone_shapes zs ON zs.id=ordered.zone_shape_id WHERE NOT suspicious
      ) """
    rows = many(
        conn,
        intervals
        + """SELECT z.id,z.name,ST_AsGeoJSON(sh.polygon)::json AS polygon,
        coalesce(sum(w.minutes),0)::float AS person_minutes,count(w.id)::int AS samples
       FROM zones z JOIN zone_shapes sh ON sh.zone_id=z.id
       JOIN site_map_versions mv ON mv.id=sh.map_version_id
       LEFT JOIN weighted w ON w.zone_id=z.id
       WHERE z.site_id=%(site)s AND mv.version=(SELECT max(version) FROM site_map_versions WHERE site_id=%(site)s)
       GROUP BY z.id,z.name,sh.polygon
    """,
        params,
    )
    coverage = one(
        conn,
        """SELECT count(*)::int AS samples,count(*) FILTER(WHERE suspicious)::int AS suspicious_samples
       FROM location_checks WHERE site_id=%s AND received_at BETWEEN %s AND %s
       AND (%s::uuid[] IS NULL OR employee_id=ANY(%s::uuid[]))""",
        (site_id, start, end, employee_ids, employee_ids),
    )
    observed = one(
        conn,
        intervals + "SELECT coalesce(sum(minutes),0)::float AS minutes FROM weighted",
        params,
    )["minutes"]
    total = sum(
        r["hours"] * 60 for r in statistics(conn, site_id, start, end, employee_ids)
    )
    coverage.update(
        observed_person_minutes=round(observed, 1),
        shift_person_minutes=round(total, 1),
        unknown_person_minutes=round(max(0, total - observed), 1),
    )
    return {
        "metric": "estimated_person_minutes",
        "max_hold_seconds": 600,
        "zones": rows,
        "coverage": coverage,
        "start": start,
        "end": end,
    }


def onsite(conn, site_id, include_photo=True):
    photo = (
        """(SELECT p.media_id FROM face_reference_photos p JOIN face_reference_sets r ON r.id=p.set_id
       WHERE r.employee_id=e.id AND r.state='active' AND p.slot=1)"""
        if include_photo
        else "NULL::uuid"
    )
    return many(
        conn,
        f"""SELECT s.id AS shift_id,e.id AS employee_id,e.full_name,s.opened_at,
      extract(epoch FROM now()-s.opened_at)/3600 AS hours,
      l.received_at AS location_at,ST_X(l.position) AS longitude,ST_Y(l.position) AS latitude,
      l.inside,l.suspicious,(l.received_at IS NULL OR l.received_at<now()-interval '12 minutes') AS location_unknown,
      {photo} AS photo_id
      FROM shifts s JOIN employees e ON e.id=s.employee_id
      LEFT JOIN LATERAL(SELECT * FROM location_checks WHERE shift_id=s.id ORDER BY received_at DESC LIMIT 1) l ON true
      WHERE s.site_id=%s AND s.closed_at IS NULL ORDER BY e.full_name""",
        (site_id,),
    )


def statistics(conn, site_id, start, end, employee_ids=None):
    return many(
        conn,
        """SELECT e.id AS employee_id,e.full_name,count(s.id)::int AS shifts,
      coalesce(sum(greatest(0,extract(epoch FROM least(coalesce(s.closed_at,now()),%s::timestamptz)
       -greatest(s.opened_at,%s::timestamptz))))/3600,0)::float AS hours
      FROM employees e JOIN shifts s ON s.employee_id=e.id WHERE s.site_id=%s
       AND s.opened_at<%s AND coalesce(s.closed_at,now())>%s
       AND (%s::uuid[] IS NULL OR e.id=ANY(%s::uuid[])) GROUP BY e.id,e.full_name ORDER BY e.full_name""",
        (end, start, site_id, end, start, employee_ids, employee_ids),
    )


def tool_query(conn, name, site_id, start, end, employee_ids=None):
    if name == "onsite_now":
        return onsite(conn, site_id, include_photo=False)
    if name == "shift_statistics":
        return statistics(conn, site_id, start, end, employee_ids)
    if name == "zone_statistics":
        return heatmap(conn, site_id, start, end, employee_ids)
    if name == "passage_denials":
        return many(
            conn,
            "SELECT reason,count(*)::int AS count FROM passage_attempts WHERE site_id=%s AND state IN ('denied','expired') AND created_at BETWEEN %s AND %s AND (%s::uuid[] IS NULL OR employee_id=ANY(%s::uuid[])) GROUP BY reason ORDER BY count DESC LIMIT 20",
            (site_id, start, end, employee_ids, employee_ids),
        )
    if name == "period_summary":
        rows = statistics(conn, site_id, start, end, employee_ids)
        return {
            "employees": len(rows),
            "shifts": sum(r["shifts"] for r in rows),
            "hours": round(sum(r["hours"] for r in rows), 2),
        }
    raise ValueError("Tool not allowed")
