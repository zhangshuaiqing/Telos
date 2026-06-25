"""记忆系统 — 工作记忆 + 情景记忆"""


class WorkingMemory:
    """工作记忆 — 当前任务上下文 (内存中)"""

    def __init__(self, max_items: int = 10):
        self._items: list[dict] = []
        self._max = max_items
        self._context: dict = {}

    def set_context(self, key: str, value) -> None:
        self._context[key] = value

    def get_context(self, key: str, default=None):
        return self._context.get(key, default)

    def add(self, event: dict) -> None:
        self._items.append(event)
        if len(self._items) > self._max:
            self._items = self._items[-self._max:]

    def recent(self, n: int = 5) -> list[dict]:
        return self._items[-n:]

    def summary(self) -> str:
        if not self._items:
            return "(无记忆)"
        return f"最近 {len(self._items)} 个事件: " + \
               "; ".join(str(e.get("action", "")) for e in self._items[-3:])

    def clear(self) -> None:
        self._items = []


class EpisodicMemory:
    """情景记忆 — 轨迹持久化 (SQLite)"""

    def __init__(self, db_path: str = "telos_memory.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    step INTEGER,
                    action_type TEXT,
                    action TEXT,
                    params TEXT,
                    result TEXT,
                    error TEXT
                )
            """)

    def record(self, step: int, action_type: str, action: dict,
               result: str = "", error: str = "") -> None:
        import json, sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO events (step, action_type, action, params, result, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (step, action_type, str(action.get("actuator", "")),
                 json.dumps(action.get("params", {})), result, error)
            )

    def query_recent(self, limit: int = 20) -> list[dict]:
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def count(self) -> int:
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
