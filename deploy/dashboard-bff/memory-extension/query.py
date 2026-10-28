"""Bounded read-only query for the explicitly supported Neo4j Community backend.

Filtering, counting and ordering execute in storage. No graph export and no vectors
or sources cross the network. Identity is the same user_name/user_id pair used by
the native writer and guarded soft deletion, not an inferred response group label.
"""
from datetime import datetime

TABS = {'text_mem', 'event_mem', 'profile_mem', 'pref_mem', 'tool_mem', 'skill_mem'}
NATIVE = "CASE n.memory_type WHEN 'UserMemory' THEN 'profile_mem' WHEN 'Context' THEN 'event_mem' WHEN 'InsightMemory' THEN 'event_mem' ELSE null END"
EXPLICIT = "coalesce(n.dashboard_type, CASE WHEN n.type IN ['text_mem','event_mem','profile_mem'] THEN n.type ELSE null END)"
BUCKETS = {
    'text_mem': "NOT coalesce(n.memory_type IN ['PreferenceMemory','ToolSchemaMemory','ToolTrajectoryMemory','SkillMemory'], false)",
    'pref_mem': "n.memory_type = 'PreferenceMemory'",
    'tool_mem': "n.memory_type IN ['ToolSchemaMemory','ToolTrajectoryMemory']",
    'skill_mem': "n.memory_type = 'SkillMemory'",
}


def parameters(body):
    user, cube, tab = body.get('user_id'), body.get('mem_cube_id'), body.get('tab')
    if any(not isinstance(x, str) or not x.strip() or len(x) > 128 for x in (user, cube)) or not isinstance(tab, str) or tab not in TABS:
        raise ValueError('Invalid query scope or tab')
    page, size = body.get('page', 1), body.get('page_size', 10)
    if type(page) is not int or not 1 <= page <= 10000000 or type(size) is not int or not 1 <= size <= 1000:
        raise ValueError('Invalid pagination')
    ids = body.get('ids')
    if ids is not None and (not isinstance(ids, list) or not 1 <= len(ids) <= 100 or
            any(not isinstance(x, str) or not 1 <= len(x) <= 128 for x in ids) or len(set(ids)) != len(ids)):
        raise ValueError('Invalid exact ID query')
    start, end = body.get('start_time'), body.get('end_time')
    def instant(value):
        if value is None or value == '':
            return None
        if not isinstance(value, str):
            raise ValueError('Invalid timestamp')
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Timestamp requires timezone')
        return parsed
    low, high = instant(start), instant(end)
    if low and high and low > high:
        raise ValueError('Reversed time range')
    for key in ('search', 'external_user_id', 'subject_type'):
        value = body.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 8000):
            raise ValueError('Invalid filter')
    if body.get('subject_type') and body['subject_type'] not in ('user', 'agent', 'public'):
        raise ValueError('Invalid subject')
    return dict(user=user, cube=cube, tab=tab, ids=ids, skip=0 if ids else (page-1)*size,
                size=len(ids) if ids else size, preview=max(64, 120000 // size // 4) + 128,
                start=low.isoformat() if low else None, end=high.isoformat() if high else None,
                search=(body.get('search') or '').lower(), external=body.get('external_user_id') or '',
                subject=body.get('subject_type') or '')


def statement(tab):
    semantic = f'coalesce({EXPLICIT}, {NATIVE})'
    bucket = BUCKETS.get(tab, f'{semantic} = $tab')
    scope = "n.user_name = $cube AND n.user_id = $user AND coalesce(n.status, 'activated') = 'activated'"
    conflict = f"(n.dashboard_type IS NOT NULL AND NOT n.dashboard_type IN ['text_mem','event_mem','profile_mem']) OR ({EXPLICIT} IS NOT NULL AND {NATIVE} IS NOT NULL AND {EXPLICIT} <> {NATIVE}) OR (n.dashboard_type IS NOT NULL AND n.type IN ['text_mem','event_mem','profile_mem'] AND n.dashboard_type <> n.type)"
    where = f"""{scope} AND ({bucket})
      AND ($ids IS NULL OR n.id IN $ids)
      AND ($start IS NULL OR n.created_at >= datetime($start))
      AND ($end IS NULL OR n.created_at <= datetime($end))
      AND ($external = '' OR coalesce(n.user_id,n.user_name,'') = $external)
      AND ($subject = '' OR CASE WHEN n.subject_type IN ['user','agent','public'] THEN n.subject_type ELSE 'user' END = $subject)
      AND ($search = '' OR toLower(coalesce(n.memory,'') + ' ' + coalesce(n.skill_name,'')) CONTAINS $search)"""
    # The conflict guard also examines records excluded by a contradictory tab marker.
    return f"""
    CALL {{ MATCH (n:Memory) WHERE {scope} AND ($ids IS NULL OR n.id IN $ids)
      RETURN count(CASE WHEN {conflict} THEN 1 END) AS conflicts }}
    CALL {{ MATCH (n:Memory) WHERE {where} RETURN count(n) AS total }}
    CALL {{ MATCH (n:Memory) WHERE {where}
      WITH n ORDER BY coalesce(n.created_at,datetime('0001-01-01T00:00:00Z')) DESC, n.id ASC SKIP $skip LIMIT $size
      RETURN collect({{id:n.id, memory_truncated:($ids IS NULL AND size(coalesce(n.memory,'')) > $preview), memory:CASE WHEN $ids IS NULL THEN substring(coalesce(n.memory,''),0,$preview) ELSE n.memory END,
        metadata:{{type:n.type, dashboard_type:n.dashboard_type, memory_type:n.memory_type,
          status:n.status, user_id:n.user_id, user_name:n.user_name, subject_type:n.subject_type,
          created_at:toString(n.created_at), skill_name:n.skill_name, skill_url:n.skill_url,
          profile_template_id:n.profile_template_id, profile_instance_id:n.profile_instance_id}}}}) AS rows }}
    RETURN total, rows, conflicts
    """


def query(graph, body):
    p = parameters(body)
    with graph.driver.session(database=graph.db_name) as session:
        result = session.execute_read(lambda tx: tx.run(statement(p['tab']), **p).single())
    if result is None or result['conflicts']:
        raise ValueError('Native/explicit classification conflict')
    return {'rows': result['rows'], 'total': result['total'], 'user_id': p['user'], 'mem_cube_id': p['cube']}
