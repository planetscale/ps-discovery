import logging
from typing import Any, Dict, List, Optional


class SafeCursor:
    def __init__(self, connection, logger: Optional[logging.Logger] = None):
        self.connection = connection
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self.errors: List[str] = []

    def execute(self, sql: str, params: Optional[tuple] = None) -> bool:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, params) if params else cursor.execute(sql)
        except Exception as exc:
            self.rollback()
            error = f"{sql.split()[0].lower()}: {exc}"
            self.errors.append(error)
            self.logger.warning(error)
            return False
        return True

    def commit(self) -> None:
        try:
            self.connection.commit()
        except Exception:
            pass

    def commit_or_raise(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        try:
            self.connection.rollback()
        except Exception:
            pass

    def one(self, sql: str, params: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params) if params else cursor.execute(sql)
            row = cursor.fetchone()
        return dict(row) if row else None

    def one_or_none(
        self, sql: str, params: Optional[tuple] = None
    ) -> Optional[Dict[str, Any]]:
        try:
            return self.one(sql, params)
        except Exception:
            self.rollback()
            return None

    def all(self, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql, params) if params else cursor.execute(sql)
            rows = cursor.fetchall()
        return [dict(row) for row in rows]
