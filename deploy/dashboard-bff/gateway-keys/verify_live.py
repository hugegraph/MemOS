"""Staged real BFF/MCP key acceptance inside our gateway; never print/store tokens.

prepare -> restart ONLY BFF -> verify -> cleanup. Empty native cube registration
remains after cleanup. No memories, model calls, original-token revocation.
"""
import argparse
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
MCP = 'http://127.0.0.1:8003/mcp'
stage = 'initialization'
calls = 0


def request(url, body=None, expected=200):
    global calls
    calls += 1
    assert calls <= 60
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=25) as response:
        assert response.status == expected
        result = json.load(response)
        assert result.get('code') in (0, 200)
        return result['data']


def bff(path, body=None):
    return request(BFF + path, body)


def demo_fingerprint():
    data = request('http://memos:8000/product/get_memory', {'user_id': 'dashboard-demo', 'mem_cube_id': 'dashboard-demo', 'page': None, 'page_size': None})
    rows = [(r['id'], r['memory'], r.get('metadata', {}).get('status')) for group in data['text_mem'] for r in group['memories']]
    return hashlib.sha256(json.dumps(sorted(rows)).encode()).hexdigest()


def key_list(project):
    return bff('keys/list', {'projectId': project, 'pageSize': 100})['records']


async def profile(token):
    async with Client(MCP, auth=token) as client:
        result = await client.call_tool('get_user_profile', {'include_preference': False, 'include_tool_memory': False, 'current': 1, 'size': 50})
        assert not result.is_error
        value = result.data
        if isinstance(value, dict) and isinstance(value.get('result'), str):
            value = value['result']
        if isinstance(value, str):
            value = json.loads(value)
        assert isinstance(value, dict) and isinstance(value.get('total'), int)
        return value


def assert_revoked(token):
    body = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'dashboard-key-verify', 'version': '1'}}}
    req = urllib.request.Request(MCP, data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raise AssertionError('Revoked key was not rejected')
    except urllib.error.HTTPError as error:
        assert error.code == 401


async def run(args):
    global stage
    path = Path(args.manifest)
    assert path.parent == Path('/tmp') and path.name.startswith('keys5-') and path.suffix == '.json'
    m = {}
    report = {'phase': args.phase, 'status': 'FAIL', 'memoryWrites': 0, 'modelCalls': 0}
    def persist():
        temp = path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(m, output)
        temp.replace(path)
    try:
        if args.phase == 'prepare':
            assert not path.exists()
            before = bff('project/dict')
            original = key_list('dashboard-demo')
            assert original
            m.update(run='keys5-' + uuid.uuid4().hex[:8], before=before, demo=demo_fingerprint(), originalIds=sorted(k['id'] for k in original), originalId=original[0]['id'], state='preparing')
            persist()
            stage = 'original-token-real-call'
            await profile(original[0]['apiKey'])
            stage = 'create-owned-project'
            project = bff('project/create', {'name': m['run'], 'description': 'Dedicated key activation acceptance'})
            assert project['name'] == m['run'] and project['id'] not in [p['id'] for p in before]
            m['project'] = project['id']; persist()
            stage = 'create-key'
            key = bff('keys/create', {'projectId': m['project'], 'keyName': m['run']})
            m['keyId'] = key['id']; persist()
            assert bff('keys/copy', {'apiKeyId': key['id']}) == key['apiKey']
            assert [k['id'] for k in key_list(m['project'])] == [key['id']]
            assert sorted(k['id'] for k in key_list('dashboard-demo')) == m['originalIds']
            stage = 'immediate-new-token-real-call'
            assert (await profile(key['apiKey']))['total'] == 0
            m['state'] = 'prepared'; persist()
            report['checks'] = ['original token valid', 'create/list/copy scoped to user+cube', 'new token immediately authenticates and reads own empty cube']
        else:
            m.update(json.loads(path.read_text()))
            assert m['run'].startswith('keys5-') and m.get('project') not in [p['id'] for p in m['before']]
            if args.phase == 'verify':
                assert m['state'] == 'prepared'
                stage = 'post-bff-restart-key-persistence'
                keys = key_list(m['project'])
                key = next(k for k in keys if k['id'] == m['keyId'])
                assert key['keyName'] == m['run']
                assert bff('keys/copy', {'apiKeyId': key['id']}) == key['apiKey']
                assert (await profile(key['apiKey']))['total'] == 0
                stage = 'revoke-and-deny-immediately'
                bff('keys/revoke/' + key['id'], {})
                m['revoked'] = True; persist()
                assert_revoked(key['apiKey'])
                assert not key_list(m['project'])
                original = key_list('dashboard-demo')
                assert sorted(k['id'] for k in original) == m['originalIds']
                await profile(next(k['apiKey'] for k in original if k['id'] == m['originalId']))
                assert demo_fingerprint() == m['demo']
                m['state'] = 'verified'; persist()
                report['checks'] = ['BFF restart preserves key and metadata', 'revoked token immediately HTTP401', 'original token and demo memories unchanged']
            else:
                stage = 'owned-fixture-cleanup'
                projects = bff('project/dict')
                current = next((p for p in projects if p['id'] == m.get('project')), None)
                if current:
                    assert current['name'] == m['run']
                    for key in key_list(m['project']):
                        assert key['id'] == m.get('keyId') and key['keyName'] == m['run']
                        bff('keys/revoke/' + key['id'], {})
                        assert_revoked(key['apiKey'])
                    bff('project/revoke/' + m['project'], {})
                assert sorted(p['id'] for p in bff('project/dict')) == sorted(p['id'] for p in m['before'])
                assert sorted(k['id'] for k in key_list('dashboard-demo')) == m['originalIds']
                assert demo_fingerprint() == m['demo']
                m['state'] = 'cleaned'; persist()
                report['checks'] = ['test key revoked', 'test mapping removed', 'original key set and demo fingerprint preserved']
                report['note'] = 'Empty native cube registration remains; no hard delete'
        report['status'] = 'PASS'
    except Exception as error:
        report['failure'] = {'stage': stage, 'type': type(error).__name__}
        if m:
            persist()
    report['httpCalls'] = calls
    print(json.dumps(report))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'verify', 'cleanup'])
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--allow-write', action='store_true', required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(asyncio.wait_for(run(args), 180)))
