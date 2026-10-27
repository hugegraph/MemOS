"""Durable batch receipts; upstream intents remain the memory source of truth."""

import json

from .errors import ApiError


class OperationStore:
    def __init__(self, control):
        self.control = control
        with control.connection() as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS operation_records (id TEXT PRIMARY KEY, "
                "project_id TEXT NOT NULL, fingerprint TEXT NOT NULL, document TEXT NOT NULL)"
            )

    def get(self, identifier, project):
        with self.control.connection() as connection:
            row = connection.execute(
                "SELECT document FROM operation_records WHERE id=? AND project_id=?",
                (identifier, project),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def prepare(self, record):
        encoded = json.dumps(record, ensure_ascii=False)
        with self.control.connection() as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO operation_records VALUES (?,?,?,?)",
                (record["operationId"], record["projectId"], record["fingerprint"], encoded),
            )
            row = connection.execute(
                "SELECT project_id,fingerprint,document FROM operation_records WHERE id=?",
                (record["operationId"],),
            ).fetchone()
            if row[0] != record["projectId"] or row[1] != record["fingerprint"]:
                raise ApiError(40900, "operationId 已用于其他操作", status_code=409)
        return json.loads(row[2])

    def merge_result(self, record, result):
        with self.control.connection() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = json.loads(
                connection.execute(
                    "SELECT document FROM operation_records WHERE id=? AND project_id=?",
                    (record["operationId"], record["projectId"]),
                ).fetchone()[0]
            )
            for index, item in enumerate(stored["results"]):
                if item["memoryId"] == result["memoryId"] and item["state"] != "confirmed":
                    stored["results"][index] = result
            connection.execute(
                "UPDATE operation_records SET document=? WHERE id=? AND project_id=?",
                (
                    json.dumps(stored, ensure_ascii=False),
                    record["operationId"],
                    record["projectId"],
                ),
            )
        return stored
