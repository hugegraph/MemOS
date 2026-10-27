"""Incremental, transactional JSONL index. Sources are operator configuration only."""

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import orjson

from .audit_normalize import UnscopedLine, normalize
from .errors import ApiError
from .memory_models import time_boundary
from .timefmt import display
from .usage_models import midnight

MAX_LINE_BYTES = 1024 * 1024


def compact(value):
    return orjson.dumps(value).decode("utf-8")


def unavailable():
    return ApiError(50300, "审计日志来源暂不可用", status_code=503)


class AuditIndex:
    def __init__(self, path: Path, sources: dict[str, Path]):
        self.path, self.sources = path, sources
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS audit_files (
                    source TEXT, identity TEXT, generation TEXT, offset INTEGER,
                    prefix_len INTEGER, prefix_sha TEXT, tail_sha TEXT,
                    PRIMARY KEY(source,identity));
                CREATE TABLE IF NOT EXISTS audit_lines (
                    generation TEXT, position INTEGER, call_key TEXT,
                    PRIMARY KEY(generation,position));
                CREATE TABLE IF NOT EXISTS audit_rejects (
                    generation TEXT, position INTEGER, reason TEXT,
                    PRIMARY KEY(generation,position));
                CREATE TABLE IF NOT EXISTS audit_calls (
                    id TEXT PRIMARY KEY, cube TEXT, source TEXT, event TEXT, status TEXT,
                    ts INTEGER, users TEXT, agents TEXT, search_text TEXT,
                    detail TEXT, request_record TEXT, terminal_record TEXT);
                CREATE INDEX IF NOT EXISTS audit_tenant_time ON audit_calls(cube,ts DESC,id DESC);
                CREATE INDEX IF NOT EXISTS audit_source_time
                    ON audit_calls(cube,source,ts DESC,id DESC);
                CREATE INDEX IF NOT EXISTS audit_event_time
                    ON audit_calls(cube,event,ts DESC,id DESC);
                CREATE INDEX IF NOT EXISTS audit_status_time
                    ON audit_calls(cube,status,ts DESC,id DESC);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        # The authoritative JSONL can rebuild this cache. NORMAL preserves WAL
        # transaction atomicity; after power loss both rows and cursor may replay.
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA cache_size=-32768")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _anchors(file, offset):
        length = min(offset, 4096)
        file.seek(0)
        prefix = hashlib.sha256(file.read(length)).hexdigest()
        file.seek(offset - length)
        tail = hashlib.sha256(file.read(length)).hexdigest()
        return length, prefix, tail

    def sync(self, scope="all"):
        totals = {"imported": 0, "invalid": 0, "unscoped": 0}
        names = tuple(self.sources) if scope == "all" else (scope,)
        if not names:
            raise unavailable()
        try:
            # Validate every selected source before importing any of them.
            files = []
            for source in names:
                folder = self.sources.get(source)
                if folder is None or not folder.is_dir():
                    raise unavailable()
                prefix = "calls-" if source == "gateway" else "console-"
                for path in sorted(folder.iterdir()):
                    if not re.fullmatch(
                        re.escape(prefix) + r"[^/\\]+\.jsonl(?:\.[1-9][0-9]*)?", path.name
                    ):
                        continue
                    if path.is_symlink() or not path.is_file():
                        raise unavailable()
                    files.append((source, path))
            for source, path in files:
                counts = self._sync_file(source, path)
                for key in totals:
                    totals[key] += counts[key]
        except (OSError, sqlite3.Error) as exc:
            raise unavailable() from exc
        return totals

    def _sync_file(self, source, path):
        counts = {"imported": 0, "invalid": 0, "unscoped": 0}
        with path.open("rb") as file, self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            stat = os.fstat(file.fileno())
            identity = f"{stat.st_dev}:{stat.st_ino}"
            if os.name == "nt":
                identity += f":{stat.st_ctime_ns}"
            cursor = db.execute(
                "SELECT * FROM audit_files WHERE source=? AND identity=?", (source, identity)
            ).fetchone()
            offset = cursor["offset"] if cursor else 0
            generation = cursor["generation"] if cursor else uuid.uuid4().hex
            if cursor and offset:
                file.seek(0)
                prefix = hashlib.sha256(file.read(cursor["prefix_len"])).hexdigest()
                file.seek(max(0, offset - min(offset, 4096)))
                tail = hashlib.sha256(file.read(min(offset, 4096))).hexdigest()
                if (
                    stat.st_size < offset
                    or prefix != cursor["prefix_sha"]
                    or tail != cursor["tail_sha"]
                ):
                    offset, generation = 0, uuid.uuid4().hex
            file.seek(offset)
            while file.tell() < stat.st_size:
                position = file.tell()
                raw = file.readline(min(MAX_LINE_BYTES + 1, stat.st_size - position))
                oversized = len(raw) > MAX_LINE_BYTES
                while oversized and not raw.endswith(b"\n") and file.tell() < stat.st_size:
                    raw = file.readline(min(MAX_LINE_BYTES + 1, stat.st_size - file.tell()))
                if not raw.endswith(b"\n"):
                    break  # The cursor stays at the start of a partial line.
                offset = file.tell()
                if oversized:
                    counts["invalid"] += 1
                    db.execute(
                        "INSERT OR IGNORE INTO audit_rejects VALUES (?,?,?)",
                        (generation, position, "invalid"),
                    )
                    continue
                try:
                    entry = orjson.loads(raw)
                    record = normalize(entry, source)
                    counts["imported"] += self._store_line(db, generation, position, record)
                except UnscopedLine:
                    counts["unscoped"] += 1
                    db.execute(
                        "INSERT OR IGNORE INTO audit_rejects VALUES (?,?,?)",
                        (generation, position, "unscoped"),
                    )
                except (ValueError, TypeError, RecursionError):
                    counts["invalid"] += 1
                    db.execute(
                        "INSERT OR IGNORE INTO audit_rejects VALUES (?,?,?)",
                        (generation, position, "invalid"),
                    )
            if os.fstat(file.fileno()).st_size < offset:
                raise OSError("Audit file changed during import")
            anchors = self._anchors(file, offset)
            db.execute(
                "INSERT OR REPLACE INTO audit_files VALUES (?,?,?,?,?,?,?)",
                (source, identity, generation, offset, *anchors),
            )
        return counts

    def _store_line(self, db, generation, position, record):
        call_id = record["call_id"]
        key = (
            "\0".join((record["source"], record["cube"], call_id))
            if call_id
            else f"{generation}:{position}"
        )
        identifier = "log_" + hashlib.sha256(key.encode()).hexdigest()
        existing = (
            db.execute(
                "SELECT request_record,terminal_record,event FROM audit_calls WHERE id=?",
                (identifier,),
            ).fetchone()
            if call_id
            else None
        )
        if existing and record["event"] and existing["event"] not in (record["event"], "unknown"):
            raise ValueError("Conflicting events for a call ID")
        if not db.execute(
            "INSERT OR IGNORE INTO audit_lines VALUES (?,?,?)", (generation, position, identifier)
        ).rowcount:
            return 0
        request = (
            json.loads(existing["request_record"])
            if existing and existing["request_record"]
            else None
        )
        terminal = (
            json.loads(existing["terminal_record"])
            if existing and existing["terminal_record"]
            else None
        )
        if record["phase"] == "terminal":
            terminal = self._merge_terminal(terminal, record)
        elif request is None or record["ts"] < request["ts"]:
            request = record
        base, final = request or terminal, terminal or request
        users = sorted(set((request or {}).get("users", []) + (terminal or {}).get("users", [])))
        agents = sorted(set((request or {}).get("agents", []) + (terminal or {}).get("agents", [])))
        event = (request or {}).get("event") or (terminal or {}).get("event") or "unknown"
        detail = {
            "id": identifier,
            "createTime": base["time"],
            "eventType": event,
            "status": final["status"],
            "duration": final["duration"],
            "requestTokens": final["request_tokens"],
            "responseTokens": final["response_tokens"],
            "requestBody": base["request_body"],
            "responseBody": final["response_body"],
            "source": record["source"],
        }
        if users:
            detail["userId"] = ",".join(users)
        if agents:
            detail["agentId"] = ",".join(agents)
        for output, field in (
            ("callId", "call_id"),
            ("traceId", "trace_id"),
            ("requestId", "request_id"),
            ("requestCode", "request_code"),
            ("responseCode", "response_code"),
            ("llmCalls", "llm_calls"),
        ):
            value = final[field] if final[field] is not None else base[field]
            if value is not None:
                detail[output] = value
        search = " ".join(
            (event, call_id or "", *users, *agents, detail["requestBody"], detail["responseBody"])
        ).casefold()
        db.execute(
            "INSERT OR REPLACE INTO audit_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                record["cube"],
                record["source"],
                event,
                final["status"],
                base["ts"],
                compact(users),
                compact(agents),
                search,
                compact(detail),
                compact(request) if request and call_id else None,
                compact(terminal) if terminal and call_id else None,
            ),
        )
        return 1

    @staticmethod
    def _merge_terminal(previous, incoming):
        if previous is None:
            return incoming
        # Later explicit evidence wins; an equal-time conflict prefers FAILURE.
        newer, older = (
            (incoming, previous)
            if (incoming["ts"], incoming["status"] == "FAILURE")
            >= (previous["ts"], previous["status"] == "FAILURE")
            else (previous, incoming)
        )
        merged = dict(newer)
        if newer["status"] == "UNKNOWN":
            merged["status"] = older["status"]
        for field in (
            "duration",
            "request_tokens",
            "response_tokens",
            "request_body",
            "response_body",
        ):
            if field not in newer.get("observed", []) and field in older.get("observed", []):
                merged[field] = older[field]
        merged["observed"] = sorted(set(newer.get("observed", []) + older.get("observed", [])))
        for field in (
            "event",
            "trace_id",
            "request_id",
            "request_code",
            "response_code",
            "llm_calls",
        ):
            if merged[field] is None:
                merged[field] = older[field]
        for field in ("users", "agents"):
            merged[field] = sorted(set(newer[field] + older[field]))
        return merged

    def usage(self, cubes, start, end, *, include_daily=True):
        """Aggregate existing logical rows; no second reader or status-based billing guess."""
        events = ("addMessage", "searchMemory", "chat", "extractMemory", "rerank")

        def empty():
            return {
                event: {"calls": 0, "totalTokens": 0}
                if event in {"addMessage", "searchMemory", "rerank"}
                else {"requestTokens": 0, "responseTokens": 0, "totalTokens": 0}
                for event in events
            }

        days = {
            (start + timedelta(days=n)).isoformat(): empty() for n in range((end - start).days + 1)
        }
        summary = empty()
        # Read all selected cubes in one SQLite snapshot. Date bounds are UTC
        # microseconds, bucket labels follow the frontend's fixed UTC+8 calendar.
        with self.connection() as db:
            db.execute("BEGIN")
            for cube in dict.fromkeys(cubes):
                rows = db.execute(
                    "SELECT strftime('%Y-%m-%d',"
                    "(ts-CASE WHEN ts<0 THEN 999999 ELSE 0 END)/1000000,"
                    "'unixepoch','+8 hours') AS day,"
                    "event,json_extract(detail,'$.requestTokens') AS req,"
                    "json_extract(detail,'$.responseTokens') AS res "
                    "FROM audit_calls WHERE cube=? AND source='gateway' AND ts>=? AND ts<? "
                    "AND json_extract(detail,'$.createTime')!='' "
                    "AND event IN ('addMessage','searchMemory','chat','extractMemory','rerank')",
                    (
                        cube,
                        round(midnight(start).timestamp() * 1_000_000),
                        round(midnight(end + timedelta(days=1)).timestamp() * 1_000_000),
                    ),
                )
                for row in rows:
                    if row["day"] not in days:
                        continue
                    req, res = int(row["req"] or 0), int(row["res"] or 0)
                    for bucket in (days[row["day"]][row["event"]], summary[row["event"]]):
                        bucket["totalTokens"] += req + res
                        if "calls" in bucket:
                            bucket["calls"] += 1
                        else:
                            bucket["requestTokens"] += req
                            bucket["responseTokens"] += res
        summary["totalCalls"] = summary["addMessage"]["calls"] + summary["searchMemory"]["calls"]
        summary["totalTokens"] = sum(summary[event]["totalTokens"] for event in events)
        return {
            "dailyStats": [{"date": day, **value} for day, value in days.items()]
            if include_daily
            else [],
            "summary": summary,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "timezone": "Asia/Shanghai",
            "source": "gateway",
        }

    def page(self, cube, query):
        clauses, values = ["cube=?"], [cube]
        for column, value in (
            ("source", query.scope if query.scope != "all" else None),
            ("event", query.eventType),
            ("status", query.status),
        ):
            if value is not None:
                clauses.append(column + "=?")
                values.append(value)
        if query.query:
            clauses.append("instr(search_text,?)>0")
            values.append(query.query.casefold())
        if query.subjectId:
            columns = (
                ("users",)
                if query.subjectType == "user"
                else ("agents",)
                if query.subjectType == "agent"
                else ("users", "agents")
            )
            clauses.append(
                "("
                + " OR ".join(
                    f"EXISTS(SELECT 1 FROM json_each({col}) WHERE value=?)" for col in columns
                )
                + ")"
            )
            values.extend([query.subjectId] * len(columns))
        for raw, end in ((query.startTime, False), (query.endTime, True)):
            date, exclusive = time_boundary(raw, end=end)
            if date is not None:
                clauses.append("ts" + ("<" if exclusive else "<=" if end else ">=") + "?")
                values.append(round(date.timestamp() * 1_000_000))
        where = " AND ".join(clauses)
        with self.connection() as db:
            db.execute("BEGIN")
            total = db.execute(
                "SELECT count(*) FROM audit_calls WHERE " + where, values
            ).fetchone()[0]
            rows = db.execute(
                "SELECT detail,users,agents FROM audit_calls WHERE "
                + where
                + " ORDER BY ts DESC,id DESC LIMIT ? OFFSET ?",
                (*values, query.pageSize, (query.pageNum - 1) * query.pageSize),
            ).fetchall()
        logs = []
        for row in rows:
            detail = json.loads(row["detail"])
            served = {
                key: value
                for key, value in detail.items()
                if key
                not in {
                    "requestBody",
                    "responseBody",
                    "userId",
                    "agentId",
                    "traceId",
                    "requestId",
                    "requestCode",
                    "responseCode",
                    "llmCalls",
                }
            } | {"userIds": json.loads(row["users"]), "agentIds": json.loads(row["agents"])}
            if served.get("createTime"):
                served["createTime"] = display(served["createTime"])
            logs.append(served)
        return {"logs": logs, "total": total}

    def detail(self, cube, identifier):
        with self.connection() as db:
            row = db.execute(
                "SELECT detail FROM audit_calls WHERE cube=? AND id=?", (cube, identifier)
            ).fetchone()
        if row is None:
            return None
        served = json.loads(row[0])
        if served.get("createTime"):
            served["createTime"] = display(served["createTime"])
        return served
