"""Disposable Neo4j integration only. No production MemOS/Qdrant acceptance claim.

Run with --allow-fixture-write and NEO4J_TEST_URI/user/password. URI must be
loopback. Only this run's UUID-owned nodes are created or cleaned.
"""
import json
import os
import sys
import time
from uuid import uuid4
from types import SimpleNamespace
from urllib.parse import urlparse
from query import query

if '--allow-fixture-write' not in sys.argv or not os.getenv('NEO4J_TEST_URI'):
    print(json.dumps({'status':'BLOCKED','reason':'Requires disposable loopback Neo4j and explicit fixture-write opt-in'}))
    sys.exit(2)
uri = os.environ['NEO4J_TEST_URI']
if urlparse(uri).hostname not in ('127.0.0.1','localhost'):
    raise RuntimeError('Disposable loopback service required')
from neo4j import GraphDatabase

owner='review-'+str(uuid4()); cube=owner+'-cube'
driver=GraphDatabase.driver(uri,auth=(os.getenv('NEO4J_TEST_USER','neo4j'),os.environ['NEO4J_TEST_PASSWORD']))
graph=SimpleNamespace(driver=driver,db_name='neo4j')
try:
    for attempt in range(60):
        try:
            driver.verify_connectivity(); break
        except Exception:
            if attempt == 59: raise
            time.sleep(1)
    body=dict(user_id=owner,mem_cube_id=cube,tab='text_mem',page=1,page_size=10)
    with driver.session() as session:
        # >32MiB of source content, independent of the BFF's projection budget.
        for start in range(0,10000,500):
            session.run('UNWIND $ids AS id CREATE (:Memory {id:id,user_name:$cube,user_id:$user,status:"activated",type:"fact",memory_type:"LongTermMemory",memory:$memory,created_at:datetime("2026-10-01T00:00:00Z")})',
                        ids=[owner+'-'+str(i).zfill(5) for i in range(start,start+500)],cube=cube,user=owner,memory='needle '+('abcd'*1100)).consume()
        for native in ('UserMemory','Context','InsightMemory','PreferenceMemory','ToolSchemaMemory','SkillMemory'):
            session.run('CREATE (:Memory {id:$id,user_name:$cube,user_id:$user,status:"activated",type:"fact",memory_type:$native,memory:$native,created_at:datetime("2026-10-02T00:00:00Z")})',
                        id=owner+'-'+native,cube=cube,user=owner,native=native).consume()
    first=query(graph,{**body,'search':'needle'})
    assert first['total']==10000 and len(first['rows'])==10
    assert len(json.dumps(first).encode())<200000
    second=query(graph,{**body,'search':'needle','page':2})
    assert [r['id'] for r in second['rows']]==[owner+'-'+str(i).zfill(5) for i in range(10,20)]
    assert all(r['memory_truncated'] for r in first['rows'])
    detail=query(graph,{**body,'ids':[owner+'-00000']})
    assert detail['rows'][0]['memory']=='needle '+('abcd'*1100)
    assert query(graph,{**body,'tab':'profile_mem'})['total']==1
    assert query(graph,{**body,'tab':'event_mem'})['total']==2
    for tab in ('pref_mem','tool_mem','skill_mem'):
        assert query(graph,{**body,'tab':tab})['total']==1
    assert query(graph,{**body,'user_id':'foreign'})['total']==0
    assert query(graph,{**body,'mem_cube_id':'foreign'})['total']==0
    assert query(graph,{**body,'search':'needle','start_time':'2026-10-02T00:00:00Z'})['total']==0
    assert query(graph,{**body,'search':'needle','end_time':'2026-10-01T00:00:00Z'})['total']==10000
    with driver.session() as session:
        session.run('MATCH (n:Memory {id:$id,user_name:$cube,user_id:$user}) SET n.dashboard_type="event_mem"',id=owner+'-UserMemory',cube=cube,user=owner).consume()
    try: query(graph,{**body,'tab':'profile_mem'})
    except ValueError: pass
    else: raise AssertionError('Conflicting labels must not be hidden')
    print(json.dumps({'status':'PASS','scope':'disposable Neo4j query integration','sourceRows':10006,'pageBytes':len(json.dumps(first).encode())}))
finally:
    try:
        with driver.session() as session:
            session.run('MATCH (n:Memory {user_name:$cube,user_id:$user}) DETACH DELETE n',cube=cube,user=owner).consume()
    finally: driver.close()
