import os
import json
import threading
from collections import defaultdict
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'
db_engine = create_engine(DATABASE_URL)


class PostgresStore:
    def __init__(self):
        self._logs = defaultdict(list)
        self._status = {}
        self._lock = threading.Lock()

    def append_log(self, task_id, line):
        with self._lock:
            self._logs[task_id].append(line)
        try:
            with db_engine.connect() as conn:
                conn.execute(text("""
                    UPDATE research_tasks
                    SET logs = COALESCE(logs, '[]'::jsonb) || :line::jsonb
                    WHERE id = :task_id::uuid
                """), {'task_id': str(task_id), 'line': json.dumps(line)})
                conn.commit()
        except Exception:
            pass

    def get_logs(self, task_id):
        with self._lock:
            local_logs = list(self._logs.get(task_id, []))
        if local_logs:
            return local_logs
        try:
            with db_engine.connect() as conn:
                result = conn.execute(text("""
                    SELECT logs FROM research_tasks WHERE id = :task_id::uuid
                """), {'task_id': str(task_id)})
                row = result.fetchone()
                if row and row[0]:
                    return row[0]
        except Exception:
            pass
        return []

    def set_status(self, task_id, status):
        with self._lock:
            self._status[task_id] = status

    def get_status(self, task_id):
        with self._lock:
            return self._status.get(task_id, 'running')


task_store = PostgresStore()