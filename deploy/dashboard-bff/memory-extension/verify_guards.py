"""Run inside task MemOS: invalid private requests only, no valid deletion.

Caller supplies only manifest-owned IDs/cubes over stdin. Credential remains in-process.
Independent graph/vector snapshots before/after are taken by the browser orchestrator.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from uuid import uuid4

p = json.load(sys.stdin)
token = os.getenv('DASHBOARD_MEMORY_TOKEN') or os.environ['DASHBOARD_PROMPT_TOKEN']
base = {'user_id': p['user'], 'mem_cube_id': p['cube'], 'memory_ids': [p['owned']]}
checks = [
    ('unauthenticated', base, None, 403),
    ('wrong-user', {**base, 'user_id': 'unowned-' + uuid4().hex}, token, 404),
    ('mixed-foreign-cube', {**base, 'memory_ids': [p['owned'], p['other']]}, token, 404),
    ('mixed-unknown', {**base, 'memory_ids': [p['owned'], str(uuid4())]}, token, 404),
    ('empty-batch', {**base, 'memory_ids': []}, token, 400),
]
results = []
for name, body, credential, expected in checks:
    headers = {'Content-Type': 'application/json'}
    if credential:
        headers['X-Dashboard-Prompt-Token'] = credential
    request = urllib.request.Request('http://127.0.0.1:8000/product/dashboard-memory/soft-delete', data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    if status != expected:
        print(json.dumps({'status': 'FAIL', 'case': name, 'http': status, 'expected': expected}))
        sys.exit(1)
    results.append({'case': name, 'http': status})
print(json.dumps({'status': 'PASS', 'checks': results, 'note': 'Verify unchanged native snapshots separately'}))
