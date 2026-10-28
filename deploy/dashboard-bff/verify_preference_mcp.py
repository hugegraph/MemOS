"""Run inside the owned gateway: actual MCP extraction, native/BFF readback, scoped cleanup.

One synthetic conversation only. No SDK seeding, no relabeling, no saved tokens.
"""
import asyncio
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

logging.disable(logging.CRITICAL)
from fastmcp import Client

BFF = os.environ['DASHBOARD_TEST_BFF'].rstrip('/') + '/api/dashboard/'
NATIVE = 'http://memos:8000'
MCP = 'http://127.0.0.1:8003/mcp'


def request(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
        assert r.status == 200 and data['code'] in (0, 200)
        return data['data']


def bff(path, body=None):
    return request(BFF + path, body)


def native(user, cube):
    return request(NATIVE + '/product/get_memory', {'user_id': user, 'mem_cube_id': cube,
        'page': None, 'page_size': None, 'include_preference': True,
        'include_tool_memory': True, 'include_skill_memory': True})


def bucket(data, name, cube):
    return [r for group in data[name] if group['cube_id'] == cube for r in group['memories']]


def demo_fingerprint():
    d = native('dashboard-demo', 'dashboard-demo')
    rows = [(r['id'], r['memory'], r.get('metadata', {}).get('status'))
            for key in ('text_mem', 'pref_mem', 'tool_mem', 'skill_mem')
            for r in bucket(d, key, 'dashboard-demo')]
    return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()


async def main():
    run = 'pref7-' + uuid.uuid4().hex[:8]
    path = Path('/tmp/' + run + '.json')
    m = {'run': run, 'state': 'preparing', 'before': bff('project/dict'),
         'demo': demo_fingerprint(), 'ids': []}
    report = {'kind': 'actual-mcp-preference-extraction', 'status': 'FAIL', 'run': run,
              'manifest': str(path), 'sdkSeeding': False, 'checks': []}
    def persist():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(m, f)
    persist()
    stage = 'create-owned-project'
    try:
        p = bff('project/create', {'name': run, 'description': 'Single synthetic native preference extraction test'})
        m['project'] = p['id']; persist()
        assert p['id'] not in [x['id'] for x in m['before']]
        assert all(not bucket(native('dashboard-bff-owner', p['id']), k, p['id'])
                   for k in ('text_mem', 'pref_mem', 'tool_mem', 'skill_mem'))
        stage = 'create-scoped-key'
        key = bff('keys/create', {'projectId': p['id'], 'keyName': run})
        m['keyId'] = key['id']; persist()
        stage = 'real-mcp-add'
        async with Client(MCP, auth=key['apiKey'], timeout=240) as client:
            m['addStarted'] = True; persist()
            result = await client.call_tool('add_message', {
                'conversation_first_message': run,
                'messages': [{'role': 'user', 'content': '我不吃花生。这是我的饮食偏好。以后推荐餐厅时，请不要推荐含花生的菜品。'}],
                'tags': [run]})
            m['addCompleted'] = True; persist()
            assert not result.is_error
            stage = 'native-extraction-readback'
            data = native('dashboard-bff-owner', p['id'])
            records = {r['id']: r for k in ('text_mem', 'pref_mem', 'tool_mem', 'skill_mem') for r in bucket(data, k, p['id'])}
            m['ids'] = sorted(records); persist()
            assert 0 < len(records) <= 30, 'Unexpected synthetic record count'
            prefs = bucket(data, 'pref_mem', p['id'])
            assert prefs, 'MCP success without native preference extraction'
            assert all(r['metadata']['memory_type'] == 'PreferenceMemory' for r in prefs)
            assert any('花生' in r['memory'] or 'peanut' in r['memory'].lower() for r in prefs)
            page = bff('project/memory/list', {'projectId': p['id'], 'memoryDetailType': 'pref_mem', 'pageNum': 1, 'pageSize': 100})
            assert sorted(r['memoryId'] for r in page['memListItemResponseList']) == sorted(r['id'] for r in prefs)
            for row in page['memListItemResponseList']:
                assert row['memoryContent'] == records[row['memoryId']]['memory']
            report['checks'].append('real MCP add -> native PreferenceMemory -> exact BFF pref_mem ID/content match')
            report['preferenceCount'] = len(prefs)
            report['syntheticPreferences'] = [{'id': r['id'], 'memory': r['memory']} for r in prefs]
            stage = 'real-mcp-retrieval'
            result = await client.call_tool('search_memory', {'query': '这个用户的饮食偏好是什么，推荐餐厅应该避开什么食物？', 'top_k': 10})
            assert not result.is_error
            value = json.dumps(result.data, ensure_ascii=False)
            assert '花生' in value or 'peanut' in value.lower(), 'MCP retrieval did not return dietary content'
            assert any(r['id'] in value for r in prefs), 'MCP retrieval must include an actual extracted preference ID'
            report['checks'].append('real MCP search returns dietary content')
        report['status'] = 'PASS'
    except Exception as e:
        report['failure'] = {'stage': stage, 'type': type(e).__name__}
    finally:
        try:
            if m.get('addStarted') and not m.get('addCompleted'):
                # A timed-out synchronous request may still be writing upstream.
                # Revoke further access, but retain the cube/ledger for reconciliation.
                if m.get('keyId'):
                    bff('keys/revoke/' + m['keyId'], {})
                m['state'] = 'needs-recovery'; persist()
                raise RuntimeError('Uncertain upstream completion; do not race cleanup against an in-flight add')
            if m.get('project'):
                p = next(x for x in bff('project/dict') if x['id'] == m['project'])
                assert p['name'] == run
                data = native('dashboard-bff-owner', p['id'])
                rows = {r['id']: (k, r) for k in ('text_mem', 'pref_mem', 'tool_mem', 'skill_mem') for r in bucket(data, k, p['id'])}
                assert len(rows) <= 30
                m['ids'] = sorted(rows); persist()
                for id_, (kind, _) in rows.items():
                    bff('project/memory/delete', {'memory_ids': [id_], 'memType': kind})
                remaining = native('dashboard-bff-owner', p['id'])
                assert all(not bucket(remaining, k, p['id']) for k in ('text_mem', 'pref_mem', 'tool_mem', 'skill_mem'))
                if m.get('keyId'):
                    bff('keys/revoke/' + m['keyId'], {})
                    req = urllib.request.Request(MCP, data=b'{}', headers={'Authorization': 'Bearer ' + key['apiKey'], 'Content-Type': 'application/json', 'Accept': 'application/json,text/event-stream'})
                    try:
                        urllib.request.urlopen(req, timeout=10)
                        raise AssertionError('Revoked test token accepted')
                    except urllib.error.HTTPError as e:
                        assert e.code == 401
                bff('project/revoke/' + p['id'], {})
            original = next(p for p in m['before'] if p['status'] == 'ENABLED')
            bff('project/update', {'id': original['id'], 'status': 'ENABLED'})
            assert demo_fingerprint() == m['demo'], 'Existing user memory changed'
            assert sorted(p['id'] for p in bff('project/dict')) == sorted(p['id'] for p in m['before'])
            m['state'] = 'cleaned'; persist()
            report['cleanup'] = {'status': 'PASS', 'ownedRecords': len(m['ids']), 'demoUnchanged': True, 'testKeyRevoked': True}
        except Exception as e:
            report['cleanup'] = {'status': 'FAIL', 'type': type(e).__name__}
            report['status'] = 'FAIL'
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
