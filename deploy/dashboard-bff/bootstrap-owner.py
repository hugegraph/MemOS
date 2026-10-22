"""Explicit provisioning inside MemOS container, reusing its persisted UserManager.

Does not mutate demo identity, grant admin role, or start/reconfigure a service.
"""
import json
import sys

from memos.mem_user.user_manager import UserManager, UserRole

if "--create-owner" not in sys.argv:
    raise SystemExit("Explicit --create-owner is required")

manager = UserManager()
owner_id, owner_name = "dashboard-bff-owner", "Dashboard BFF Owner"
user = manager.get_user(owner_id)
if user is None:
    created = manager.create_user(owner_name, role=UserRole.USER, user_id=owner_id)
    if created != owner_id:
        raise SystemExit("Owner name already belongs to another ID; refused")
    user = manager.get_user(owner_id)
if user.user_name != owner_name or user.role != UserRole.USER or not user.is_active:
    raise SystemExit("Existing owner differs from expected ordinary active user; refused")
print(json.dumps({"status": "PASS", "owner_id": owner_id, "role": user.role.value}))
