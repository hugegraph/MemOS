"""Scoped soft deletion for the tested Neo4j Community + Qdrant deployment.

Graph update is one guarded transaction; nodes, edges, content and vectors stay.
Vector synchronization errors are failures, never successful deletes. Retrying the
same private request is safe, including when graph state is already deleted.
"""
from datetime import datetime, timezone
from uuid import uuid4


class ScopeError(ValueError):
    pass


class RequestError(ValueError):
    pass


QUERY = """
MATCH (n:Memory)
WHERE n.id IN $ids AND n.user_name = $cube AND n.user_id = $user
  AND n.status IN ['activated', 'deleted']
WITH collect(n) AS nodes
WHERE size(nodes) = $count
FOREACH (node IN nodes |
  SET node.status = 'deleted',
      node.delete_record_id = coalesce(node.delete_record_id, $record),
      node.delete_time = coalesce(node.delete_time, $time))
RETURN size(nodes) AS count
"""


def delete(graph, user, cube, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
        raise RequestError('Expected a bounded nonempty ID list')
    if any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in [user, cube, *ids]):
        raise RequestError('Invalid identity or ID')
    if len(ids) != len(set(ids)):
        raise RequestError('Duplicate IDs')
    vec = graph.vec_db
    params = dict(ids=ids, cube=cube, user=user, count=len(ids), record=str(uuid4()), time=datetime.now(timezone.utc).isoformat())
    def mark(tx):
        row = tx.run(QUERY, **params).single()
        if row is None or row['count'] != len(ids):
            raise ScopeError('Missing, inactive or out-of-scope ID; entire batch unchanged')
        return row['count']
    with graph.driver.session(database=graph.db_name) as session:
        count = session.execute_write(mark)
    # Reuse native payload-only update. Never upsert a missing vector or alter embeddings.
    points = vec.client.retrieve(collection_name=vec.config.collection_name, ids=ids, with_payload=True, with_vectors=False)
    for point in points:
        vec.update(str(point.id), {'id': str(point.id), 'payload': {'status': 'deleted'}})
    after = vec.client.retrieve(collection_name=vec.config.collection_name, ids=ids, with_payload=True, with_vectors=False)
    if {str(p.id) for p in points} != {str(p.id) for p in after} or any(p.payload.get('status') != 'deleted' for p in after):
        raise RuntimeError('Vector status synchronization incomplete')
    return {'deleted': count, 'soft_deleted': True, 'vector_records': len(after)}
