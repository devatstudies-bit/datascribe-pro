"""
Layer 6 — Security: Read-Only SQL Enforcement
Three levels of defence — every SQL string passes through here before
reaching the database connection.
Every blocked attempt is counted in Prometheus and written to the audit log.
"""
import sqlglot
from sqlglot import expressions as exp
from src.observability.metrics import SQL_BLOCKED_TOTAL

# Statement types that mutate data or schema
_BLOCKED_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Command,      # catches EXEC / EXECUTE
    exp.Transaction,  # prevents DML wrapped in BEGIN
)

# Secondary keyword scan for edge cases not caught by AST
_BLOCKED_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "create",
    "alter", "truncate", "grant", "revoke", "exec", "execute",
})


class ReadOnlyViolationError(Exception):
    """Raised when a non-SELECT SQL statement is detected."""


class ReadOnlySQLValidator:
    """
    Validates SQL is strictly read-only before execution.

    Level 1 (AST): parse with sqlglot and block any non-SELECT node types.
    Level 2 (keywords): token scan as a secondary catch for obfuscated SQL.
    Level 3 (DB connection): enforced externally via read-only DB credentials.
    """

    def validate(self, sql: str) -> None:
        clean = sql.strip().rstrip(";").strip()
        if not clean:
            raise ReadOnlyViolationError("Empty SQL blocked.")
        self._check_ast(clean)
        self._check_keywords(clean)

    def _check_ast(self, sql: str) -> None:
        try:
            statements = sqlglot.parse(sql)
        except Exception:
            SQL_BLOCKED_TOTAL.labels(reason="unparseable").inc()
            raise ReadOnlyViolationError(f"Unparseable SQL blocked: {sql[:120]}")

        for stmt in statements:
            if stmt is None:
                continue
            if isinstance(stmt, _BLOCKED_NODE_TYPES):
                SQL_BLOCKED_TOTAL.labels(reason="ast_node").inc()
                raise ReadOnlyViolationError(
                    f"Blocked statement type: {type(stmt).__name__}"
                )
            for node in stmt.walk():
                if isinstance(node, _BLOCKED_NODE_TYPES):
                    SQL_BLOCKED_TOTAL.labels(reason="ast_node").inc()
                    raise ReadOnlyViolationError(
                        f"Blocked nested statement: {type(node).__name__}"
                    )

    def _check_keywords(self, sql: str) -> None:
        tokens = set(sql.lower().split())
        blocked = tokens & _BLOCKED_KEYWORDS
        if blocked:
            SQL_BLOCKED_TOTAL.labels(reason="keyword").inc()
            raise ReadOnlyViolationError(
                f"Blocked SQL keywords detected: {blocked}"
            )
