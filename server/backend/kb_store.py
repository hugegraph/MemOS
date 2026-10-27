"""Only namespace ownership and links live here; KB entities and bytes stay upstream."""

import asyncio
from collections import defaultdict

from .identity_store import conflict, missing


class KnowledgeStore:
    def __init__(self, control):
        self.control = control
        self.locks = defaultdict(asyncio.Lock)
        self.mutation = asyncio.Lock()
        with control.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS kb_registry ("
                "id TEXT PRIMARY KEY,"
                "home_project TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,"
                "state TEXT NOT NULL DEFAULT 'active')"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS kb_links ("
                "kb_id TEXT REFERENCES kb_registry(id) ON DELETE CASCADE,"
                "project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,"
                "PRIMARY KEY(kb_id,project_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS kb_files ("
                "id TEXT PRIMARY KEY,"
                "kb_id TEXT NOT NULL REFERENCES kb_registry(id) ON DELETE CASCADE)"
            )
            if "state" not in {r[1] for r in db.execute("PRAGMA table_info(kb_files)")}:
                db.execute("ALTER TABLE kb_files ADD COLUMN state TEXT NOT NULL DEFAULT 'active'")

    @staticmethod
    def project(store, identifier, minimum="viewer", db=None):
        if db is None:
            with store.connection() as conn:
                return KnowledgeStore.project(store, identifier, minimum, conn)
        row = store._row(db, identifier)
        if row["disabled"] or row["archived"]:
            raise missing()
        store.require_role(identifier, minimum, db)
        return row["upstream_cube_id"]

    def register(self, identifier, home):
        with self.control.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT home_project FROM kb_registry WHERE id=?", (identifier,)
            ).fetchone()
            if old and old[0] != home:
                raise conflict("知识库归属冲突")
            if not old:
                db.execute(
                    "INSERT INTO kb_registry(id,home_project) VALUES (?,?)", (identifier, home)
                )
                db.execute("INSERT INTO kb_links VALUES (?,?)", (identifier, home))

    def access(self, store, identifier, *, project=None, write=None, pending=False):
        with store.connection() as db:
            row = db.execute("SELECT * FROM kb_registry WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise missing()
            home = row["home_project"]
            links = [
                r[0]
                for r in db.execute(
                    "SELECT project_id FROM kb_links WHERE kb_id=? ORDER BY project_id",
                    (identifier,),
                )
            ]
            visible = {r["id"] for r in store.dictionary()}
            accessible = sorted(set(links) & visible)
            home_manage = home in visible and store.role(home, db) == "owner"
            if project is not None:
                self.project(store, project, db=db)
                if project not in links and not (project == home and home_manage):
                    raise missing()
            elif not accessible and not home_manage:
                raise missing()
            # Reading a shared object does not authorize unrelated home resources.
            base = self.control._row(db, home)
            if base["disabled"] or base["archived"]:
                raise missing()
            if write:
                for target in set(links) | {home}:
                    store.require_role(target, write, db)
                    self.project(store, target, write, db)
            if row["state"] != "active" and not pending:
                raise conflict("知识库删除尚未确认，请重试删除")
            return home, base["upstream_cube_id"], accessible

    def links(self, store, identifier, projects):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT home_project FROM kb_registry WHERE id=? AND state='active'", (identifier,)
            ).fetchone()
            if row is None:
                raise missing()
            home = row[0]
            old = {
                r[0]
                for r in db.execute("SELECT project_id FROM kb_links WHERE kb_id=?", (identifier,))
            }
            for target in old | set(projects) | {home}:
                self.project(store, target, "owner", db)
                kind = store._row(db, target)["kind"]
                if target != home and (
                    kind == "personal" or store._row(db, home)["kind"] == "personal"
                ):
                    raise conflict("个人空间知识库不跨项目共享")
            db.execute("DELETE FROM kb_links WHERE kb_id=?", (identifier,))
            db.executemany("INSERT INTO kb_links VALUES (?,?)", [(identifier, p) for p in projects])

    def ids(self, store, project=None):
        visible = {p["id"] for p in store.dictionary()}
        if project:
            self.project(store, project)
        with store.connection() as db:
            rows = db.execute(
                "SELECT id,home_project FROM kb_registry WHERE state='active' ORDER BY id"
            ).fetchall()
            result = []
            for row in rows:
                links = {
                    r[0]
                    for r in db.execute(
                        "SELECT project_id FROM kb_links WHERE kb_id=?", (row["id"],)
                    )
                }
                if (
                    project in links
                    if project
                    else bool(visible & links)
                    or (
                        row["home_project"] in visible
                        and store.role(row["home_project"], db) == "owner"
                    )
                ):
                    result.append(row["id"])
            return result
