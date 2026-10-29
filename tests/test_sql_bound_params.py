"""SQL values are bound parameters, never interpolated (issue #462, security map G3).

Dynamic SQL here interpolates only code-built fragments: column allow-lists, ``?``
placeholder lists, and literal clauses picked by a bool. This test makes that a rule
instead of a convention. It scans every git-tracked ``*.py`` outside ``tests/`` and
``experiments/`` and reports every expression interpolated into SQL:

- an f-string, ``str % ...``, ``"...".format(...)`` or ``+`` / ``+=`` chain whose literal
  text looks like a statement (``SELECT ... FROM``, ``INSERT INTO``, ``UPDATE x SET``,
  ``DELETE FROM``, ``WHERE``, ``VALUES (``, ``PRAGMA``), or that is appended to a
  variable holding one;
- whatever reaches the first argument of ``execute``/``executemany``, followed one hop
  to the variable's assignments in the same function, even when it doesn't look like
  SQL on its own (a fragment-only query).

Each finding must be on ``ALLOWED``, keyed by ``(path, function, expression)``, with the
reason the expression is code-built. A new interpolation fails until someone reviews
it. Never add an entry for a value to make this pass: bind it as a parameter.

``psycopg2.sql.SQL(...).format(sql.Identifier(...))`` is not flagged; it is the safe
way to compose identifiers. It is a pure AST scan, so it runs in the lean CI jobs too.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDED_DIRS = ("tests/", "experiments/")
EXECUTE_METHODS = {"execute", "executemany"}

# Uppercase only, and a two-keyword shape where one keyword alone is English
# ("no SELECT on {name}" is a message, not SQL).
SQL_SHAPE = re.compile(
    r"\bSELECT\b[\s\S]*\bFROM\b"
    r"|\bINSERT\s+(?:OR\s+\w+\s+)?INTO\b"
    r"|\bUPDATE\s+\S+\s+SET\b"
    r"|\bDELETE\s+FROM\b"
    r"|\bWHERE\b"
    r"|\bVALUES\s*\("
    r"|\bPRAGMA\b"
)

_BY_BOOL = "a literal clause picked by a bool, never caller text"
_MARKS = "one '?' per symbol; the symbols themselves are bound"
_LEGACY_IDENT = ("a table/column identifier from the script's hard-coded table list or "
                 "the source database's own schema; operator-run, never deployed")

ALLOWED = {
    ("quantcore/repositories/harvester_repository.py", "purge_superseded_plans", "where_clause"):
        "' AND '.join of literal conditions; their values are bound",
    ("quantcore/repositories/harvester_repository.py", "update_plan_metadata", "', '.join(parts)"):
        "literal 'col = ?' pieces chosen by which arguments are not None",
    ("quantcore/repositories/news_repository.py", "get_articles", "scored_clause"): _BY_BOOL,
    ("quantcore/repositories/news_repository.py", "get_unscored_articles", "' '.join(clauses)"):
        "literal 'AND col = ?' pieces; their values are bound",
    ("quantcore/repositories/news_repository.py", "get_unscored_articles", "order"):
        "'ASC' or 'DESC', picked by a bool",
    ("quantcore/repositories/news_repository.py", "count_unscored_by_day", "since_clause"):
        _BY_BOOL,
    ("quantcore/repositories/news_repository.py", "get_scored_articles_for_symbols", "marks"):
        _MARKS,
    ("quantcore/repositories/news_repository.py", "first_article_fetched_at_by_symbol",
     "marks"): _MARKS,
    ("quantcore/repositories/news_repository.py", "article_count", "clause"): _BY_BOOL,
    ("quantcore/repositories/ohlcv_repository.py", "daily_bars_for_symbols", "placeholders"):
        _MARKS,
    ("quantcore/repositories/options_repository.py", "get_oi_timeseries", "exp_filter"):
        _BY_BOOL,
    ("quantcore/repositories/portfolio_repository.py", "_apply_lot_fields", "set_clause"):
        "column names checked against MUTABLE_LOT_COLUMNS just above; values are bound",
    ("scripts/migrate_sqlite_to_postgres.py", "_reset_sequence", "pk_col"): _LEGACY_IDENT,
    ("scripts/migrate_sqlite_to_postgres.py", "_reset_sequence", "table"): _LEGACY_IDENT,
    ("scripts/migrate_sqlite_to_postgres.py", "_migrate_table", "table"): _LEGACY_IDENT,
    ("scripts/migrate_sqlite_to_postgres.py", "_migrate_table", "col_names"): _LEGACY_IDENT,
    ("scripts/migrate_sqlite_to_postgres.py", "migrate", "table"): _LEGACY_IDENT,
    ("scripts/migrate_to_unified_db.py", "count_rows", "table"): _LEGACY_IDENT,
    ("scripts/migrate_to_unified_db.py", "migrate_table", "table_name"): _LEGACY_IDENT,
    ("scripts/migrate_to_unified_db.py", "migrate_table", "col_list"): _LEGACY_IDENT,
    ("scripts/migrate_to_unified_db.py", "migrate_table", "placeholders"):
        "one '?' per column; the row values are bound",
    ("scripts/migrate_to_unified_db.py", "migrate_table", "alias"):
        "the ATTACH alias, a literal from the script's own table list",
}


def _is_str(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_str_format(node: ast.expr) -> bool:
    """``"...".format(...)`` on a str literal -- not ``sql.SQL(...).format``."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format" and _is_str(node.func.value))


def _add_operands(node: ast.expr) -> list:
    """``a + b + c`` flattened to ``[a, b, c]``."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _add_operands(node.left) + _add_operands(node.right)
    return [node]


def _literal_text(node: ast.expr) -> str:
    """The literal SQL text of a string-building expression, with the holes dropped."""
    if _is_str(node):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if _is_str(v))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _literal_text(node.left)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return "".join(_literal_text(op) for op in _add_operands(node))
    if _is_str_format(node):
        return node.func.value.value
    return ""


def _holes(node: ast.expr) -> list:
    """The expressions interpolated by a string-building expression."""
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and (
            _is_str(node.left) or isinstance(node.left, ast.JoinedStr)):
        right = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
        return _holes(node.left) + list(right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        holes = []
        for op in _add_operands(node):
            holes += _appended_holes(op)
        return holes
    if _is_str_format(node):
        return list(node.args) + [kw.value for kw in node.keywords]
    return []


def _appended_holes(node: ast.expr) -> list:
    """What a ``+`` operand or ``+=`` value interpolates: a literal nothing, a string
    expression its holes, anything else (a name, a call) itself."""
    if _is_str(node):
        return []
    if isinstance(node, (ast.JoinedStr, ast.BinOp)) or _is_str_format(node):
        return _holes(node)
    return [node]


def _function_assignments(func: ast.AST) -> dict:
    """Local name -> ``[(value, is_append)]`` for its ``=``/``+=`` assignments."""
    found: dict = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append((node.value, False))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.value is not None:
            found.setdefault(node.target.id, []).append((node.value, False))
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add) \
                and isinstance(node.target, ast.Name):
            found.setdefault(node.target.id, []).append((node.value, True))
    return found


class _Scanner(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.findings: list = []
        self._seen: set = set()
        self._consumed: set = set()
        self._scopes = [("<module>", {}, set())]

    def _report(self, holes: list) -> None:
        func = self._scopes[-1][0]
        for hole in holes:
            if id(hole) in self._seen:
                continue
            self._seen.add(id(hole))
            self.findings.append((self.filename, func, ast.unparse(hole), hole.lineno))

    def _visit_function(self, node) -> None:
        assigns = _function_assignments(node)
        sql_names = {name for name, values in assigns.items()
                     if any(SQL_SHAPE.search(_literal_text(v)) for v, _ in values)}
        self._scopes.append((node.name, assigns, sql_names))
        self.generic_visit(node)
        self._scopes.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _visit_string(self, node) -> None:
        if id(node) in self._consumed:
            self.generic_visit(node)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            for op in _add_operands(node):
                self._consumed.add(id(op))
            for inner in ast.walk(node):
                if isinstance(inner, ast.BinOp) and inner is not node:
                    self._consumed.add(id(inner))
        if SQL_SHAPE.search(_literal_text(node)):
            self._report(_holes(node))
        self.generic_visit(node)

    visit_JoinedStr = _visit_string
    visit_BinOp = _visit_string

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name) and (
                node.target.id in self._scopes[-1][2]
                or SQL_SHAPE.search(_literal_text(node.value))):
            self._consumed.add(id(node.value))
            self._report(_appended_holes(node.value))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_str_format(node) and SQL_SHAPE.search(_literal_text(node)):
            self._report(_holes(node))
        if isinstance(node.func, ast.Attribute) and node.func.attr in EXECUTE_METHODS \
                and node.args:
            query = node.args[0]
            if isinstance(query, ast.Name):
                for value, is_append in self._scopes[-1][1].get(query.id, []):
                    self._report(_appended_holes(value) if is_append else _holes(value))
            else:
                self._report(_holes(query))
        self.generic_visit(node)


def find_violations(source: str, filename: str = "<string>") -> list:
    """Every interpolation into SQL in ``source``, as ``(path, function, expr, line)``."""
    scanner = _Scanner(filename)
    scanner.visit(ast.parse(source, filename))
    return scanner.findings


def _tracked_python_files() -> list:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO_ROOT, check=True,
                         capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if p and not p.startswith(EXCLUDED_DIRS)]


class SqlBoundParamsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = _tracked_python_files()
        cls.findings = []
        for rel in cls.files:
            path = os.path.join(REPO_ROOT, rel)
            if not os.path.exists(path):   # deleted in the working tree, not yet committed
                continue
            with open(path, encoding="utf-8") as fh:
                cls.findings += find_violations(fh.read(), rel)

    def test_no_unreviewed_sql_interpolation(self):
        self.assertIn("quantcore/repositories/portfolio_repository.py", self.files,
                      "the scan found none of the files it exists to check")
        unreviewed = [f"{path}:{line} {func}: {expr}"
                      for path, func, expr, line in self.findings
                      if (path, func, expr) not in ALLOWED]
        self.assertEqual(unreviewed, [], (
            "SQL interpolates an expression nobody has reviewed (#462). Bind it as a "
            "parameter. Only if it is genuinely code-built (an identifier allow-list, a "
            "placeholder list, a literal picked by a bool), add it to ALLOWED with why:\n"
            + "\n".join(unreviewed)))

    def test_allow_list_has_no_stale_entries(self):
        live = {(path, func, expr) for path, func, expr, _ in self.findings}
        stale = sorted(key for key in ALLOWED if key not in live)
        self.assertEqual(stale, [], "ALLOWED entries that match nothing; delete them")

    def test_every_entry_has_a_reason(self):
        self.assertEqual([key for key, why in ALLOWED.items() if not why.strip()], [])


class ScannerTest(unittest.TestCase):
    """The scanner itself must catch what it claims to, or the guard is a no-op."""

    def assertFlags(self, source, expr, func="f"):
        found = {(fn, ex) for _, fn, ex, _ in find_violations(source)}
        self.assertIn((func, expr), found)

    def assertClean(self, source):
        self.assertEqual(find_violations(source), [])

    def test_flags_fstring(self):
        self.assertFlags("def f(x):\n    q = f'SELECT * FROM t WHERE id = {x}'\n", "x")

    def test_flags_percent_and_format(self):
        self.assertFlags("def f(v):\n    q = \"DELETE FROM t WHERE a = '%s'\" % v\n", "v")
        self.assertFlags("def f(v):\n    q = 'UPDATE t SET a = {}'.format(v)\n", "v")
        self.assertFlags("def f(v):\n    q = 'UPDATE t SET a = {a}'.format(a=v)\n", "v")

    def test_flags_concatenation(self):
        self.assertFlags("def f(v):\n    q = 'SELECT a FROM t WHERE b = ' + v\n", "v")
        self.assertFlags("def f(v):\n    q = 'SELECT a FROM t ' + 'WHERE b = ' + v + ''\n",
                         "v")

    def test_flags_append_to_a_sql_variable(self):
        self.assertFlags("def f(v):\n    q = 'SELECT 1 FROM t'\n    q += f' AND x = {v}'\n",
                         "v")
        self.assertFlags("def f(v):\n    q = 'SELECT 1 FROM t'\n    q += v\n", "v")

    def test_flags_fragment_reaching_execute(self):
        # No SQL keywords in the f-string; it is still the query.
        self.assertFlags("def f(conn, v):\n    sql = f'{v}'\n    conn.execute(sql)\n", "v")
        self.assertFlags("def f(cur, v):\n    cur.executemany(f'{v}', [])\n", "v")

    def test_reports_the_enclosing_function(self):
        self.assertFlags("class R:\n    def get(self, x):\n"
                         "        return f'SELECT a FROM t WHERE b = {x}'\n", "x", "get")
        self.assertFlags("q = f'SELECT a FROM t WHERE b = {x}'\n", "x", "<module>")

    def test_allows_the_safe_forms(self):
        self.assertClean(
            "def f(cur, conn, v, name, logger):\n"
            "    cur.execute('SELECT a FROM t WHERE b = %s', (v,))\n"
            "    q = 'SELECT a FROM t WHERE 1 = 1'\n"
            "    q += ' AND b = %s'\n"
            "    conn.execute(q, (v,))\n"
            "    cur.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(v), r))\n"
            "    msg = f'no SELECT on {name}'\n"
            "    other = f'has {v} on {name}'\n"
            "    logger.info('SELECT a FROM t WHERE b = %s', v)\n")


if __name__ == "__main__":
    unittest.main()
