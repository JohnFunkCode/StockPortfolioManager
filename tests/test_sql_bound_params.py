"""SQL values are bound parameters, never interpolated (issue #462, security map G3).

Dynamic SQL here interpolates only code-built fragments: column allow-lists, ``?``
placeholder lists, and literal clauses picked by a bool. This test makes that a rule
instead of a convention. It scans every git-tracked ``*.py`` outside ``tests/`` and
``experiments/`` and reports every expression interpolated into SQL:

- an f-string, ``str % ...``, ``"...".format(...)`` or ``+`` / ``+=`` chain whose literal
  text looks like a statement (``SELECT ... FROM``, ``INSERT INTO``, ``UPDATE x SET``,
  ``DELETE FROM``, ``WHERE``, ``VALUES (``, ``PRAGMA``), or that is appended to a
  variable holding one -- or ``t % ...`` / ``t.format(...)`` where ``t`` is such a
  variable;
- whatever reaches the first argument of ``execute``/``executemany``, followed one hop
  to the variable's assignments in the same scope (function or module), even when it
  doesn't look like SQL on its own: a fragment-only query, or a template from anywhere
  (``QUERY.format(...)`` on a module constant).

It is a syntactic check, not taint analysis: a value laundered through a second
variable or another function's return escapes it. Review still applies.

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
    ("quantcore/repositories/portfolio_repository.py", "list_lots_for_symbol",
     "'' if status is None else '\\n  AND p.status = :status'"):
        "a literal clause picked by `status is None`; the status is bound",
    ("quantcore/repositories/portfolio_repository.py", "list_sales",
     "'' if ticker is None else '\\n  AND s.ticker = :ticker'"):
        "a literal clause picked by `ticker is None`; the ticker is bound",
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


def _never(_node: ast.expr) -> bool:
    return False


def _always(_node: ast.expr) -> bool:
    return True


def _is_str(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_add(node: ast.expr) -> bool:
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)


def _is_format_call(node: ast.expr) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format")


def _is_composable(node: ast.expr) -> bool:
    """``sql.SQL(...)``: psycopg2's identifier-safe composition, not a str template."""
    func = getattr(node, "func", None)
    return isinstance(node, ast.Call) and getattr(func, "attr", getattr(func, "id", None)) == "SQL"


def _is_execute(node: ast.Call) -> bool:
    return (isinstance(node.func, ast.Attribute) and node.func.attr in EXECUTE_METHODS
            and bool(node.args))


def _add_operands(node: ast.expr) -> list:
    """``a + b + c`` flattened to ``[a, b, c]``."""
    if _is_add(node):
        return _add_operands(node.left) + _add_operands(node.right)
    return [node]


def _add_chain(node: ast.BinOp) -> list:
    """Every node of an ``a + b + c`` chain below its root: the inner ``+``s and operands."""
    parts = []
    for side in (node.left, node.right):
        parts.append(side)
        if _is_add(side):
            parts += _add_chain(side)
    return parts


def _template(node: ast.expr):
    """The template of ``t % args`` or ``t.format(...)``; None for anything else."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return node.left
    if _is_format_call(node):
        return node.func.value
    return None


def _template_args(node: ast.expr) -> list:
    """What ``t % args`` / ``t.format(...)`` fills the template with."""
    if isinstance(node, ast.BinOp):
        return list(node.right.elts) if isinstance(node.right, ast.Tuple) else [node.right]
    return list(node.args) + [kw.value for kw in node.keywords]


def _is_sql_template(template: ast.expr, sql_receiver) -> bool:
    """A literal template is one; a name or other expression only when ``sql_receiver``
    vouches for it (it holds SQL, or it reaches ``execute``). ``sql.SQL`` never is."""
    if _is_str(template) or isinstance(template, ast.JoinedStr):
        return True
    return not _is_composable(template) and sql_receiver(template)


def _literal_text(node: ast.expr) -> str:
    """The literal SQL text of a string-building expression, with the holes dropped."""
    if _is_str(node):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if _is_str(v))
    if _is_add(node):
        return "".join(_literal_text(op) for op in _add_operands(node))
    template = _template(node)
    return "" if template is None else _literal_text(template)


def _holes(node: ast.expr, sql_receiver=_never) -> list:
    """The expressions interpolated by a string-building expression."""
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
    if _is_add(node):
        return [hole for op in _add_operands(node) for hole in _appended_holes(op, sql_receiver)]
    template = _template(node)
    if template is None or not _is_sql_template(template, sql_receiver):
        return []
    return _holes(template, sql_receiver) + _template_args(node)


def _appended_holes(node: ast.expr, sql_receiver=_never) -> list:
    """What a ``+`` operand or ``+=`` value interpolates: a literal nothing, a string
    expression its holes, anything else (a name, a call) itself."""
    if _is_str(node):
        return []
    if isinstance(node, ast.JoinedStr) or _is_add(node):
        return _holes(node, sql_receiver)
    template = _template(node)
    if template is not None and _is_sql_template(template, sql_receiver):
        return _holes(node, sql_receiver)
    return [node]


def _own_nodes(scope: ast.AST):
    """``scope``'s nodes, not descending into nested functions (each is its own scope)."""
    for child in ast.iter_child_nodes(scope):
        yield child
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _own_nodes(child)


def _assign_targets(node: ast.Assign) -> list:
    return [(t.id, node.value, False) for t in node.targets if isinstance(t, ast.Name)]


def _annassign_targets(node: ast.AnnAssign) -> list:
    named = isinstance(node.target, ast.Name) and node.value is not None
    return [(node.target.id, node.value, False)] if named else []


def _augassign_targets(node: ast.AugAssign) -> list:
    named = isinstance(node.op, ast.Add) and isinstance(node.target, ast.Name)
    return [(node.target.id, node.value, True)] if named else []


_ASSIGNMENT_TARGETS = {ast.Assign: _assign_targets, ast.AnnAssign: _annassign_targets,
                       ast.AugAssign: _augassign_targets}


def _scope_assignments(scope: ast.AST) -> dict:
    """Local name -> ``[(value, is_append)]`` for its ``=``, ``: T =`` and ``+=``."""
    found: dict = {}
    for node in _own_nodes(scope):
        targets = _ASSIGNMENT_TARGETS.get(type(node))
        for name, value, is_append in (targets(node) if targets else []):
            found.setdefault(name, []).append((value, is_append))
    return found


class _Scanner(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.findings: list = []
        self._seen: set = set()
        self._consumed: set = set()
        self._scopes: list = []   # (name, assignments, names holding SQL)

    def _report(self, holes: list) -> None:
        func = self._scopes[-1][0]
        for hole in holes:
            if id(hole) in self._seen:
                continue
            self._seen.add(id(hole))
            self.findings.append((self.filename, func, ast.unparse(hole), hole.lineno))

    def _is_sql_name(self, node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id in self._scopes[-1][2]

    def _looks_like_sql(self, node: ast.expr) -> bool:
        if SQL_SHAPE.search(_literal_text(node)):
            return True
        template = _template(node)
        return template is not None and self._is_sql_name(template)

    def _enter(self, name: str, node: ast.AST) -> None:
        assigns = _scope_assignments(node)
        sql_names = {n for n, values in assigns.items()
                     if any(SQL_SHAPE.search(_literal_text(v)) for v, _ in values)}
        self._scopes.append((name, assigns, sql_names))
        self.generic_visit(node)
        self._scopes.pop()

    def visit_Module(self, node: ast.Module) -> None:
        self._enter("<module>", node)

    def _visit_function(self, node) -> None:
        self._enter(node.name, node)

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _visit_string(self, node) -> None:
        if id(node) not in self._consumed:
            if _is_add(node):
                self._consumed.update(id(part) for part in _add_chain(node))
            if self._looks_like_sql(node):
                self._report(_holes(node, self._is_sql_name))
        self.generic_visit(node)

    visit_JoinedStr = _visit_string
    visit_BinOp = _visit_string

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if isinstance(node.op, ast.Add) and (
                self._is_sql_name(node.target) or self._looks_like_sql(node.value)):
            self._consumed.add(id(node.value))
            self._report(_appended_holes(node.value, self._is_sql_name))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_format_call(node) and self._looks_like_sql(node):
            self._report(_holes(node, self._is_sql_name))
        if _is_execute(node):
            self._report_query(node.args[0])
        self.generic_visit(node)

    def _report_query(self, query: ast.expr) -> None:
        """What reaches ``execute`` is SQL whatever it looks like: report an expression's
        holes, or a local's, one hop back to its assignments."""
        if not isinstance(query, ast.Name):
            self._report(_holes(query, _always))
            return
        for value, is_append in self._scopes[-1][1].get(query.id, []):
            self._report(_appended_holes(value, _always) if is_append
                         else _holes(value, _always))


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

    def assertFinds(self, source, *exprs, func="f"):
        """Exactly these expressions, all in ``func`` -- no more, no fewer."""
        found = sorted((fn, ex) for _, fn, ex, _ in find_violations(source))
        self.assertEqual(found, sorted((func, ex) for ex in exprs))

    def assertClean(self, source):
        self.assertEqual(find_violations(source), [])

    def test_flags_fstring(self):
        self.assertFinds("def f(x):\n    q = f'SELECT * FROM t WHERE id = {x}'\n", "x")

    def test_flags_percent_and_format(self):
        self.assertFinds("def f(v):\n    q = \"DELETE FROM t WHERE a = '%s'\" % v\n", "v")
        self.assertFinds("def f(a, b):\n    q = \"DELETE FROM t WHERE a = '%s' AND b = '%s'\""
                         " % (a, b)\n", "a", "b")
        self.assertFinds("def f(v):\n    q = 'UPDATE t SET a = {}'.format(v)\n", "v")
        self.assertFinds("def f(v, w):\n    q = 'UPDATE t SET a = {a}, b = {}'.format(w, a=v)\n",
                         "v", "w")

    def test_flags_a_named_template(self):
        # Review of #481: a template held in a variable, not written inline.
        for fill in ("template % owner", "template.format(owner)"):
            template = "'%s'" if "%" in fill else "'{}'"
            prelude = ("def f(cur, owner):\n"
                       f"    template = \"SELECT s FROM r WHERE owner = {template}\"\n")
            with self.subTest(fill=fill, at="execute"):
                self.assertFinds(prelude + f"    cur.execute({fill})\n", "owner")
            with self.subTest(fill=fill, at="assigned query"):
                self.assertFinds(prelude + f"    q = {fill}\n    cur.execute(q)\n", "owner")
            with self.subTest(fill=fill, at="not yet executed"):
                self.assertFinds(prelude + f"    q = {fill}\n", "owner")

    def test_flags_any_template_reaching_execute(self):
        # A template from elsewhere (a module constant, an attribute) is SQL at the sink.
        self.assertFinds("def f(cur, v):\n    cur.execute(QUERY.format(clause=v))\n", "v")
        self.assertFinds("def f(cur, v):\n    q = self.query % v\n    cur.execute(q)\n", "v")

    def test_flags_concatenation(self):
        self.assertFinds("def f(v):\n    q = 'SELECT a FROM t WHERE b = ' + v\n", "v")
        self.assertFinds("def f(v):\n    q = 'SELECT a FROM t ' + 'WHERE b = ' + v + ''\n",
                         "v")

    def test_flags_append_to_a_sql_variable(self):
        self.assertFinds("def f(v):\n    q = 'SELECT 1 FROM t'\n    q += f' AND x = {v}'\n",
                         "v")
        self.assertFinds("def f(v):\n    q = 'SELECT 1 FROM t'\n    q += v\n", "v")
        self.assertFinds("def f(v):\n    q = 'SELECT 1 FROM t'\n    q += other.format(v)\n",
                         "other.format(v)")

    def test_flags_fragment_reaching_execute(self):
        # No SQL keywords in the f-string; it is still the query.
        self.assertFinds("def f(conn, v):\n    sql = f'{v}'\n    conn.execute(sql)\n", "v")
        self.assertFinds("def f(conn, v):\n    sql: str = f'{v}'\n    conn.execute(sql)\n", "v")
        self.assertFinds("def f(cur, v):\n    cur.executemany(f'{v}', [])\n", "v")
        self.assertFinds("q = f'{v}'\ncur.execute(q)\n", "v", func="<module>")

    def test_nested_function_is_its_own_scope(self):
        self.assertFinds("def outer(v):\n    q = f'{v}'\n"
                         "    def inner(cur, w):\n        q = f'{w}'\n        cur.execute(q)\n",
                         "w", func="inner")

    def test_reports_the_enclosing_function(self):
        self.assertFinds("class R:\n    def get(self, x):\n"
                         "        return f'SELECT a FROM t WHERE b = {x}'\n", "x", func="get")
        self.assertFinds("q = f'SELECT a FROM t WHERE b = {x}'\n", "x", func="<module>")

    def test_allows_the_safe_forms(self):
        self.assertClean(
            "def f(cur, conn, v, name, logger, owner):\n"
            "    cur.execute('SELECT a FROM t WHERE b = %s', (v,))\n"
            "    q = 'SELECT a FROM t WHERE 1 = 1'\n"
            "    q += ' AND b = %s'\n"
            "    conn.execute(q, (v,))\n"
            "    template = 'SELECT s FROM r WHERE owner = %s'\n"
            "    cur.execute(template, (owner,))\n"
            "    cur.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(v), r))\n"
            "    stmt = sql.SQL('DROP DATABASE {}').format(sql.Identifier(name))\n"
            "    cur.execute(stmt)\n"
            "    msg = f'no SELECT on {name}'\n"
            "    other = f'has {v} on {name}'\n"
            "    text = 'hello {}'.format(name) + '%d rows' % v\n"
            "    logger.info('SELECT a FROM t WHERE b = %s', v)\n")


if __name__ == "__main__":
    unittest.main()
