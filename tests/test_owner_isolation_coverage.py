"""Owner isolation for every per-owner table (#460, security gap G1).

The rule (CLAUDE.md, "Unified Database"): every statement that touches a
per-owner table restricts it to one owner in the SQL, a child table is
restricted through its owning ancestor, ``owner`` is never a defaulted
argument, and each table has wrong-owner read and mutation tests.

This module keeps that rule from depending on memory. No database, no imports
of the code under test -- it reads ``db/schema_snapshot.json`` and the source.

1. **Inventory.** The per-owner tables are derived from the snapshot: every
   table with an ``owner`` column, plus every table with a foreign key into
   one, transitively. Each must be in ``OWNER_SCOPED`` (naming its wrong-owner
   tests, which must exist) or in ``EXEMPT`` with a reason. A new per-owner
   table therefore fails here until someone writes its tests down.

2. **SQL scan.** Every SQL string literal in the repository (outside
   ``tests/``) that reads or writes a per-owner table must restrict it to a
   bound owner. This is a *heuristic tripwire, not the proof*: it tokenizes
   SQL text, it doesn't parse it, and the behavioural wrong-owner tests named
   in (1) are what the security map cites as the guard. What it checks:

   - SELECT / UPDATE / DELETE on a table with an ``owner`` column: a
     ``WHERE`` or ``JOIN ... ON`` clause at that table's query level binds its
     ``owner`` to a parameter (``owner = %s``, ``= ?``, ``= :owner``,
     ``= %(owner)s``; qualified by alias or bare when it's the only owner
     table there). ``owner`` in the select list, ``SET``, ``ORDER BY``,
     ``GROUP BY`` or ``RETURNING`` doesn't count, nor do ``<>``, ``!=`` or
     ``IS NOT NULL``. A top-level ``OR`` in that clause fails, since it can
     widen the restriction.
   - INSERT into such a table: ``owner`` is in the column list (an omitted
     column falls through to ``DEFAULT 'john'``).
   - Every reference counts: each alias of a self-join, and each branch of a
     ``UNION``/``INTERSECT``/``EXCEPT``, is restricted on its own.
   - A child table (no ``owner`` column): each reference to it is tied to a
     restricted owning ancestor, either joined to it at the same level
     (``child.col = parent.col``, the parent restricted) or filtered by
     ``col IN (subquery)`` where the subquery restricts the ancestor (or
     names a child under it, which the scan then checks in turn). Joining
     the parent without restricting it isn't enough, and neither is an
     ancestor restricted in an uncorrelated ``EXISTS`` or sibling subquery,
     which filters none of the child's rows. The scan doesn't verify that the
     joined columns are the foreign key.
   - A statement the scan can't classify (a ``{}`` f-string hole in its
     ``WHERE``, or a ``NOT`` around an owner predicate) fails, so a new shape
     gets a human look.

   Statements that are cross-owner on purpose, or that the scan can't see
   through, go in ``ALLOWED`` keyed by file and a fingerprint of the
   normalized SQL, each with its reason. Changing the statement changes the
   fingerprint, which drops it off the list and fails here again.

3. **Signatures.** No public method of a per-owner repository defaults
   ``owner``.
"""

import ast
import hashlib
import json
import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "db" / "schema_snapshot.json"

# --------------------------------------------------------------------------
# 1. Inventory
# --------------------------------------------------------------------------

# table -> the wrong-owner tests that prove its isolation: (test file, test name).
OWNER_SCOPED = {
    "positions": [
        ("tests/test_portfolio_repository.py", "test_get_lot_is_owner_scoped"),
        ("tests/test_portfolio_repository.py", "test_update_lot_is_owner_scoped"),
        ("tests/test_portfolio_repository.py", "test_delete_lot_is_owner_scoped"),
    ],
    "lot_sales": [
        ("tests/test_portfolio_repository.py", "test_update_sale_notes_is_owner_scoped"),
        ("tests/test_api_smoke.py", "test_sales_are_owner_scoped"),
    ],
    "plan_instances": [
        ("tests/test_harvester_repository_db.py", "test_wrong_owner_reads_come_back_empty"),
        ("tests/test_harvester_repository_db.py", "test_wrong_owner_mutations_change_nothing"),
    ],
    "plan_rungs": [
        ("tests/test_harvester_repository_db.py", "test_wrong_owner_reads_come_back_empty"),
        ("tests/test_harvester_repository_db.py", "test_wrong_owner_mutations_change_nothing"),
    ],
    "alerts": [
        ("tests/test_harvester_service.py", "test_ensure_next_rung_alert_writes_nothing_for_another_owner"),
    ],
    "user_settings": [
        ("tests/test_repositories_db.py", "test_owners_are_isolated"),
        ("tests/test_settings_api.py", "test_owners_are_isolated"),
    ],
    "options_positions": [
        ("tests/test_repositories_db.py", "test_wrong_owner_reads_come_back_empty"),
        ("tests/test_repositories_db.py", "test_wrong_owner_mutations_change_nothing"),
        ("tests/test_options_position_alerts.py", "test_every_read_and_mutation_restricts_to_the_owner"),
    ],
}

EXEMPT = {
    "owner_identities": (
        "the principal -> owner mapping itself: it is read by identity to "
        "*find* the owner (api/auth.py require_owner), so there is no owner "
        "to restrict it to yet"
    ),
}

_FK = re.compile(r"FOREIGN KEY \(\w+\) REFERENCES (\w+)\(")


def _snapshot_tables() -> dict:
    return json.loads(SNAPSHOT.read_text())["tables"]


def owner_column_tables(tables: dict) -> set:
    return {name for name, t in tables.items() if "owner" in t["columns"]}


def parents(tables: dict) -> dict:
    """table -> the tables its foreign keys reference (self-references dropped)."""
    out = {}
    for name, t in tables.items():
        refs = {m.group(1) for c in t.get("constraints", {}).values() for m in [_FK.match(c)] if m}
        out[name] = refs - {name}
    return out


def _owner_ancestors(name: str, up: dict, own: set, seen: tuple = ()) -> set:
    """Owner-column tables reachable from ``name`` by foreign keys. Each path
    stops at its first owner-column table: that table's own owner is the
    restriction (plan_rungs -> plan_instances, not on to positions)."""
    found = set()
    for p in up.get(name, ()):
        if p in seen:
            continue
        if p in own:
            found.add(p)
        else:
            found |= _owner_ancestors(p, up, own, seen + (name,))
    return found


def owner_scoped_tables(tables: dict) -> dict:
    """Every per-owner table -> its owning ancestors that carry an owner column
    (empty for a table that has the column itself)."""
    own = owner_column_tables(tables)
    up = parents(tables)
    scoped = {name: set() for name in own}
    for name in set(tables) - own:
        anc = _owner_ancestors(name, up, own)
        if anc:
            scoped[name] = anc
    return scoped


def _test_functions(path: Path) -> set:
    tree = ast.parse(path.read_text())
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.scoped = owner_scoped_tables(_snapshot_tables())

    def test_every_per_owner_table_is_mapped_or_exempt(self):
        unmapped = sorted(set(self.scoped) - set(OWNER_SCOPED) - set(EXEMPT))
        self.assertEqual(
            unmapped, [],
            f"per-owner tables with no wrong-owner tests on record: {unmapped}. "
            "Add owner predicates and wrong-owner read/mutation tests, then map "
            "the table in OWNER_SCOPED (or EXEMPT it with a reason).",
        )

    def test_no_stale_entries(self):
        stale = sorted((set(OWNER_SCOPED) | set(EXEMPT)) - set(self.scoped))
        self.assertEqual(stale, [], f"mapped tables that are no longer per-owner: {stale}")

    def test_named_wrong_owner_tests_exist(self):
        cache = {}
        for table, tests in OWNER_SCOPED.items():
            self.assertTrue(tests, f"{table} names no wrong-owner tests")
            for file, name in tests:
                path = REPO / file
                funcs = cache.setdefault(file, _test_functions(path) if path.exists() else set())
                with self.subTest(table=table, test=f"{file}::{name}"):
                    self.assertIn(name, funcs)

    def test_derivation_finds_the_child_tables(self):
        # Pins the FK walk: if this breaks, the inventory above is blind.
        self.assertEqual(self.scoped["lot_sales"], {"positions"})
        self.assertEqual(self.scoped["plan_rungs"], {"plan_instances"})
        self.assertIn("plan_instances", self.scoped["alerts"])
        self.assertEqual(self.scoped["options_positions"], set())
        self.assertNotIn("watchlist", self.scoped)  # global by design (#83)


# --------------------------------------------------------------------------
# 2. SQL scan
# --------------------------------------------------------------------------

_PARAM = r"(?:%s|\?|:\w+|%\(\w+\)s)"
_RESTRICT = re.compile(r"(?:\b(\w+)\.)?\bowner\s*(?<![<>!])=\s*" + _PARAM)
_TABLE_REF = re.compile(r"\b(from|join|into|update)\s+(\w+)(?:\s+(?:as\s+)?(\w+))?")
_DML = re.compile(r"^\s*(select|insert|update|delete|with)\b")
_SET_OP = re.compile(r"\b(?:union|intersect|except)\b(?:\s+all)?")
_IN_SUBQUERY = re.compile(r"(?:\b(\w+)\.)?\b(\w+)\s+in\s*\(<subquery:(\d+)>\)")
_NOT_ALIAS = {
    "where", "join", "on", "set", "values", "left", "right", "inner", "outer",
    "cross", "full", "order", "group", "limit", "returning", "using", "select",
    "natural", "having", "union", "and", "or", "default", "lateral",
}
HOLE = "{}"


def normalize(sql: str) -> str:
    """Lowercase, comments and string literals stripped, whitespace collapsed."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    return " ".join(sql.lower().split())


def fingerprint(sql: str) -> str:
    return hashlib.sha1(normalize(sql).encode()).hexdigest()[:12]


class _Scope:
    """One query level: its own text (each subquery replaced by a numbered
    marker) and its subquery children."""

    def __init__(self, text: str, children: list, sid: int = -1):
        self.text = text
        self.children = children
        self.id = sid

    def refs(self) -> list:
        """(table, name) for each table this level names directly; ``name`` is
        its alias, or the table itself when it has none."""
        out = []
        for m in _TABLE_REF.finditer(self.text):
            table, alias = m.group(2), m.group(3)
            out.append((table, alias if alias and alias not in _NOT_ALIAS else table))
        return out

    def tables(self) -> dict:
        """alias-or-name -> table, for tables this level names directly."""
        out = {}
        for table, name in self.refs():
            out[table] = table
            out[name] = table
        return out

    def predicate_region(self) -> str:
        """Text of the WHERE clause and JOIN ... ON conditions at this level."""
        parts = re.split(r"\b(where|on)\b", self.text)
        region, keep = [], False
        for part in parts:
            if part in ("where", "on"):
                keep = True
                continue
            if keep:
                # Stop at clauses that can mention owner without restricting it.
                part = re.split(r"\b(order by|group by|returning|limit|having|do update)\b", part)[0]
                # An ON CONFLICT target is a column list, not a predicate.
                if part.lstrip().startswith("conflict"):
                    keep = False
                    continue
                region.append(part)
        return " ".join(region)

    def branches(self) -> list:
        """This level split at UNION / INTERSECT / EXCEPT: each branch reads
        rows on its own, so each must restrict them on its own."""
        return [_Scope(t, self.children, self.id) for t in _SET_OP.split(self.text)]

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def parse_scopes(sql: str) -> _Scope:
    """Split ``sql`` (normalized) into query levels at parenthesized SELECTs.
    Other parentheses (column lists, VALUES, IN lists, calls) stay inline.
    Markers are numbered across the statement, so one survives being inlined
    into its parent's text."""
    count = [0]

    def build(text, i):
        buf, children = [], []
        while i < len(text):
            ch = text[i]
            if ch == "(":
                inner, j = build(text, i + 1)
                if re.match(r"^\s*(select|with)\b", inner.text):
                    inner.id = count[0]
                    count[0] += 1
                    children.append(inner)
                    buf.append(f"(<subquery:{inner.id}>)")
                else:
                    buf.append("(" + inner.text + ")")
                    children.extend(inner.children)
                i = j
                continue
            if ch == ")":
                return _Scope("".join(buf), children), i + 1
            buf.append(ch)
            i += 1
        return _Scope("".join(buf), children), i

    scope, _ = build(sql, 0)
    return scope


def _restricted_refs(scope: _Scope, owner_tables: set) -> set:
    """Names (aliases) of owner-column references that ``scope`` binds to an owner."""
    refs = [(t, n) for t, n in scope.refs() if t in owner_tables]
    found = {_restricted_name(m.group(1), refs) for m in _RESTRICT.finditer(scope.predicate_region())}
    return found - {None}


def _restricted_name(qual, refs: list):
    """The reference an ``owner = ?`` qualified by ``qual`` (None: bare)
    restricts: the alias it names, a table named once, or the level's only
    owner table. None when it's ambiguous or names nothing here."""
    if qual is None:
        return refs[0][1] if len(refs) == 1 else None
    if qual in {n for _, n in refs}:
        return qual
    same = [n for t, n in refs if t == qual]
    return same[0] if len(same) == 1 else None


def _restricted_in(scope: _Scope, owner_tables: set) -> set:
    """Owner-column tables every reference to which ``scope`` restricts -- a
    self-join with one unrestricted alias doesn't count."""
    refs = [(t, n) for t, n in scope.refs() if t in owner_tables]
    done = _restricted_refs(scope, owner_tables)
    return {t for t, _ in refs} - {t for t, n in refs if n not in done}


def _insert_problem(table: str, columns: str, scoped: dict):
    if scoped[table]:
        return f"INSERT into child table {table}: its owner can't be bound here"
    if "owner" not in [c.strip() for c in columns.split(",")]:
        return f"INSERT into {table} without an owner column"
    return None


def _joined(child: str, ancestor: str, region: str) -> bool:
    """A column of ``child`` equated to a column of ``ancestor``."""
    c, a = re.escape(child), re.escape(ancestor)
    return bool(re.search(rf"\b{c}\.\w+\s*=\s*{a}\.\w+|\b{a}\.\w+\s*=\s*{c}\.\w+", region))


def _vouches(sub: _Scope, ancestors: set, scoped: dict, owner_tables: set) -> bool:
    """``sub`` restricts one of ``ancestors`` itself, or names a child table
    under one of them (checked as its own level by the walk)."""
    if _restricted_in(sub, owner_tables) & ancestors:
        return True
    return any(scoped.get(t, set()) & ancestors for t, _ in sub.refs())


def _in_subquery_credit(name: str, scope: _Scope, ancestors: set, scoped: dict, owner_tables: set) -> bool:
    """``name.col IN (subquery)`` (``col`` alone when this level names one
    table) where the subquery vouches for an owning ancestor."""
    subs = {c.id: c for c in scope.children}
    single = len(scope.refs()) == 1
    for m in _IN_SUBQUERY.finditer(scope.predicate_region()):
        qual, col = m.group(1), m.group(2)
        mine = qual == name if qual else (single and col != "not")
        sub = subs.get(int(m.group(3)))
        if mine and sub and _vouches(sub, ancestors, scoped, owner_tables):
            return True
    return False


def _child_credited(name: str, ancestors: set, scope: _Scope, scoped: dict, owner_tables: set) -> bool:
    """The child reference ``name`` is tied to a restricted owning ancestor:
    joined to it at this level, or filtered by an IN subquery that restricts it.
    An ancestor restricted somewhere else in the statement (an EXISTS, a
    sibling subquery) doesn't count -- it filters nothing about these rows."""
    done = _restricted_refs(scope, owner_tables)
    region = scope.predicate_region()
    joined = [n for t, n in scope.refs() if t in ancestors and n in done]
    if any(_joined(name, a, region) for a in joined):
        return True
    return _in_subquery_credit(name, scope, ancestors, scoped, owner_tables)


def _read_or_write_problem(table: str, scoped: dict, scope: _Scope, owner_tables: set):
    ancestors = scoped[table]
    if not ancestors:
        if table in _restricted_in(scope, owner_tables):
            return None
        return f"{table} is not restricted to a bound owner"
    names = {n for t, n in scope.refs() if t == table}
    if all(_child_credited(n, ancestors, scope, scoped, owner_tables) for n in names):
        return None
    return (
        f"child table {table} not tied (by a join or an IN subquery) to a "
        f"restriction on its owner ancestor ({', '.join(sorted(ancestors))})"
    )


def _negates_owner(region: str) -> bool:
    """A NOT applied to an owner predicate: ``NOT owner = ?`` or ``NOT (...owner...)``."""
    if re.search(r"\bnot\s+(?:\w+\.)?owner\b", region):
        return True
    for m in re.finditer(r"\bnot\s*\(", region):
        depth, i = 1, m.end()
        while i < len(region) and depth:
            depth += {"(": 1, ")": -1}.get(region[i], 0)
            i += 1
        if re.search(r"\bowner\b", region[m.end():i]):
            return True
    return False


def _clause_problems(region: str) -> list:
    """Problems that make a WHERE/ON clause untrustworthy as a whole."""
    if HOLE in region:
        return ["unclassifiable: an f-string hole in the WHERE clause"]
    if _negates_owner(region):
        return ["unclassifiable: a NOT around an owner predicate"]
    if re.search(r"\bor\b", region):
        return ["an OR in the WHERE clause can widen the owner restriction"]
    return []


def _check_branch(scope: _Scope, scoped: dict, owner_tables: set) -> list:
    named = {t for t, _ in scope.refs() if t in scoped}
    if not named:
        return []
    problems = _clause_problems(scope.predicate_region())
    if problems and problems[0].startswith("unclassifiable"):
        return problems
    insert = re.match(r"^\s*insert into (\w+)\s*\(([^)]*)\)", scope.text)
    inserted = insert.group(1) if insert else None
    for table in sorted(named):
        problem = (
            _insert_problem(table, insert.group(2), scoped) if table == inserted
            else _read_or_write_problem(table, scoped, scope, owner_tables)
        )
        if problem:
            problems.append(problem)
    return problems


def _check_scope(scope: _Scope, scoped: dict, owner_tables: set) -> list:
    return [p for b in scope.branches() for p in _check_branch(b, scoped, owner_tables)]


def check_statement(sql: str, scoped: dict) -> list:
    """Problems with one SQL statement; [] when it passes. ``scoped`` is
    owner_scoped_tables(): table -> owner-column ancestors."""
    owner_tables = {t for t, anc in scoped.items() if not anc}
    problems = []
    for scope in parse_scopes(normalize(sql)).walk():
        problems.extend(_check_scope(scope, scoped, owner_tables))
    return problems


# Statements that are cross-owner on purpose, or that the scan can't see
# through: (file, fingerprint) -> why it is safe. A changed statement gets a
# new fingerprint and fails until it is reviewed again.
ALLOWED = {
    # init_schema() backfills: one-time, all-owner data repairs that run with
    # the DDL on startup (the V4 mirror). They fill NULL columns; they read
    # nothing back and never cross one owner's data into another's.
    ("quantcore/db.py", "d984362a5e0c"): "_SCHEMA backfill: positions.status NULL -> default",
    ("quantcore/db.py", "d989b931450b"): "_SCHEMA backfill: positions.acquisition_type NULL -> default",
    ("quantcore/db.py", "76838b70090a"): "_SCHEMA backfill: positions.trade_date from purchase_date",
    ("quantcore/repositories/portfolio_repository.py", "f5a609927c87"): (
        "SELECT DISTINCT owner FROM positions (list_owners): the daily job's walk "
        "over every owner's symbols for the options capture (main.py). Returns "
        "owner names only, no position data."
    ),
    ("quantcore/repositories/portfolio_repository.py", "871b12c740ba"): (
        "INSERT INTO lot_sales (SQL_INSERT_SALE): insert_sale and close_lots first "
        "read the lot with SQL_GET_LOT, owner-scoped, on the same connection, and "
        "raise ValueError if it isn't this owner's."
    ),
    ("quantcore/repositories/harvester_repository.py", "34e97f75474c"): (
        "INSERT INTO alerts (SQL_UPSERT_ALERT_FOR_RUNG): rung_id/instance_id come "
        "from SQL_GET_NEXT_PENDING_RUNG, owner-scoped, on the same connection in "
        "_ensure_next_rung_alert, which returns early when it finds nothing."
    ),
    ("quantcore/repositories/harvester_repository.py", "d94bcd23409f"): (
        "INSERT INTO plan_rungs (_insert_rungs, from build_plan): instance_id is "
        "the plan_instances row this transaction just inserted with the caller's owner."
    ),
    # purge_superseded_plans builds one WHERE from a conditions list that
    # always contains "owner = ?" (params[0] is the owner); the scan can't see
    # through the f-string hole, so each of its three statements is listed.
    ("quantcore/repositories/harvester_repository.py", "68bf1aa60b21"): "purge_superseded_plans COUNT",
    ("quantcore/repositories/harvester_repository.py", "7a58ee9c608e"): "purge_superseded_plans id list",
    ("quantcore/repositories/harvester_repository.py", "4593f2e56d6b"): "purge_superseded_plans DELETE",
}


def _fstring_text(node: ast.JoinedStr) -> str:
    return "".join(v.value if isinstance(v, ast.Constant) else HOLE for v in node.values)


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _string_literals(tree: ast.AST) -> list:
    """(line, text) for every string literal; an f-string counts once, with
    ``{}`` where its holes were (its literal pieces aren't counted again)."""
    fstrings = [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]
    pieces = {id(v) for f in fstrings for v in f.values}
    found = [(f.lineno, _fstring_text(f)) for f in fstrings]
    found += [(n.lineno, n.value) for n in ast.walk(tree) if _is_str(n) and id(n) not in pieces]
    return found


def _sql_literals(path: Path):
    """(line, statement) for each ;-separated piece of a literal that starts like DML."""
    for line, text in _string_literals(ast.parse(path.read_text())):
        for stmt in text.split(";"):
            if _DML.match(normalize(stmt)):
                yield line, stmt


_SKIP_DIRS = {"tests", ".git", ".venv", "venv", "node_modules", "frontend", "build", "dist"}


def _source_files():
    """Tracked .py files outside tests/; a directory walk where there's no git."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        out = [
            p.relative_to(REPO).as_posix() for p in REPO.rglob("*.py")
            if not _SKIP_DIRS & set(p.relative_to(REPO).parts)
        ]
    return [REPO / f for f in out if not f.startswith("tests/")]


def _scoped_statements(scoped: dict):
    """(file, line, statement) for every statement naming a non-exempt per-owner table."""
    checked = set(scoped) - set(EXEMPT)
    for path in _source_files():
        rel = path.relative_to(REPO).as_posix()
        for line, stmt in _sql_literals(path):
            if {m.group(2) for m in _TABLE_REF.finditer(normalize(stmt))} & checked:
                yield rel, line, stmt


def scan_repository(scoped: dict):
    """(file, line, fingerprint, problems, sql) for every failing statement."""
    checked = {t: a for t, a in scoped.items() if t not in EXEMPT}
    for rel, line, stmt in _scoped_statements(scoped):
        problems = check_statement(stmt, checked)
        if problems:
            yield rel, line, fingerprint(stmt), problems, normalize(stmt)


class SqlScanTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.scoped = owner_scoped_tables(_snapshot_tables())

    def test_every_statement_restricts_to_a_bound_owner(self):
        failures, used = [], set()
        for rel, line, fp, problems, norm in scan_repository(self.scoped):
            if (rel, fp) in ALLOWED:
                used.add((rel, fp))
                continue
            failures.append(f"{rel}:{line} [{fp}] {'; '.join(problems)}\n    {norm[:200]}")
        self.assertEqual(
            failures, [],
            "SQL on a per-owner table without an owner restriction. Add the "
            "predicate, or -- if it is cross-owner on purpose -- an ALLOWED "
            "entry with the reason:\n" + "\n".join(failures),
        )
        self.assertEqual(sorted(set(ALLOWED) - used), [], "stale ALLOWED entries")


# Fixtures for the scanner itself: it must reject these and accept those.
SCOPED_FIXTURE = {
    "positions": set(), "options_positions": set(), "plan_instances": set(),
    "lot_sales": {"positions"}, "plan_rungs": {"plan_instances"},
}

MUST_REJECT = [
    "SELECT owner, symbol FROM options_positions",
    "SELECT * FROM options_positions ORDER BY owner",
    "SELECT ls.* FROM lot_sales ls JOIN positions p ON p.position_id = ls.lot_id",
    "UPDATE options_positions SET status = 'CLOSED' WHERE position_id = %s",
    "DELETE FROM options_positions WHERE position_id = %s",
    "INSERT INTO options_positions (symbol, kind) VALUES (%s, %s)",
    "SELECT * FROM positions WHERE owner IS NOT NULL",
    "SELECT * FROM positions WHERE owner <> %s",
    "SELECT * FROM positions WHERE owner != %s",
    "UPDATE positions SET owner = %s WHERE position_id = %s",
    "SELECT * FROM positions WHERE owner = %s OR 1 = 1",
    "SELECT * FROM positions WHERE position_id = %s -- AND owner = %s",
    "SELECT * FROM positions WHERE notes = 'owner = %s'",
    "SELECT * FROM plan_instances WHERE {}",
    "SELECT * FROM plan_rungs WHERE instance_id = ?",
    "INSERT INTO plan_rungs (instance_id, rung_index) VALUES (:instance_id, :rung_index)",
    # The restriction is on the subquery's own table, not on the outer one.
    "SELECT * FROM positions WHERE position_id IN (SELECT position_id FROM plan_instances WHERE owner = ?)",
    # An ancestor restricted in an uncorrelated subquery filters nothing (#482 review).
    "SELECT ls.sale_id FROM lot_sales ls WHERE EXISTS (SELECT 1 FROM positions p WHERE p.owner = ?)",
    "SELECT ls.* FROM lot_sales ls, positions p WHERE p.owner = ?",
    "SELECT * FROM lot_sales WHERE lot_id NOT IN (SELECT position_id FROM positions WHERE owner = ?)",
    # Negation inverts the restriction.
    "SELECT position_id FROM positions WHERE NOT (owner = ?)",
    "SELECT position_id FROM positions WHERE NOT owner = ?",
    "SELECT position_id FROM positions WHERE status = 'OPEN' AND NOT (symbol = ? AND owner = ?)",
    # Each set-operation branch reads rows on its own.
    "SELECT symbol FROM positions WHERE owner = ? UNION SELECT symbol FROM positions",
    "SELECT symbol FROM positions WHERE owner = ? UNION ALL SELECT symbol FROM options_positions",
    # Every alias of an owner table must be restricted.
    "SELECT b.* FROM positions a JOIN positions b ON a.symbol = b.symbol WHERE a.owner = ?",
    "SELECT ls.* FROM lot_sales ls JOIN positions p ON p.position_id = ls.lot_id "
    "JOIN lot_sales ls2 ON ls2.sale_id <> ls.sale_id WHERE p.owner = ?",
]

MUST_ACCEPT = [
    "SELECT * FROM options_positions WHERE position_id = %s AND owner = %s",
    "UPDATE options_positions SET status = 'EXPIRED' WHERE position_id = ANY(%s) AND owner = %s",
    "INSERT INTO options_positions (owner, symbol) VALUES (%s, %s)",
    "SELECT ls.* FROM lot_sales ls JOIN positions p ON p.position_id = ls.lot_id WHERE p.owner = :owner",
    "UPDATE lot_sales SET notes = :notes WHERE sale_id = :sale_id "
    "AND lot_id IN (SELECT position_id FROM positions WHERE owner = :owner)",
    "SELECT s.ticker FROM symbols s LEFT JOIN plan_instances ap "
    "ON ap.symbol_id = s.symbol_id AND ap.owner = ? ORDER BY s.ticker",
    "SELECT * FROM positions WHERE owner = %(owner)s",
    "SELECT pr.* FROM plan_rungs pr JOIN plan_instances pi ON pi.instance_id = pr.instance_id "
    "WHERE pr.rung_id = ? AND pi.owner = ?",
    "UPDATE plan_instances SET status = 'CLOSED', notes = CASE WHEN :r IS NULL OR notes = '' "
    "THEN notes ELSE :r END WHERE owner = :owner AND status = 'ACTIVE'",
    "SELECT symbol FROM positions WHERE owner = ? UNION SELECT symbol FROM options_positions WHERE owner = ?",
    "SELECT b.* FROM positions a JOIN positions b ON a.symbol = b.symbol WHERE a.owner = ? AND b.owner = ?",
    "SELECT position_id FROM positions WHERE owner = ? AND NOT EXISTS (SELECT 1 FROM lot_sales ls "
    "WHERE ls.lot_id IN (SELECT position_id FROM positions WHERE owner = ?))",
    "SELECT * FROM positions WHERE owner = ? AND status IS NOT NULL",
    # A child reached through another child: the inner level vouches for the outer.
    "SELECT * FROM plan_rungs WHERE instance_id IN (SELECT instance_id FROM plan_instances WHERE owner = ?)",
]


class ScannerFixtureTests(unittest.TestCase):
    def test_rejects(self):
        for sql in MUST_REJECT:
            with self.subTest(sql=sql):
                self.assertTrue(check_statement(sql, SCOPED_FIXTURE), "accepted")

    def test_accepts(self):
        for sql in MUST_ACCEPT:
            with self.subTest(sql=sql):
                self.assertEqual(check_statement(sql, SCOPED_FIXTURE), [])

    def test_fingerprint_ignores_layout_but_not_content(self):
        a = "SELECT *\n  FROM positions -- note\n WHERE owner = ?"
        self.assertEqual(fingerprint(a), fingerprint("select * from positions where owner = ?"))
        self.assertNotEqual(fingerprint(a), fingerprint("SELECT * FROM positions WHERE owner = ? OR 1=1"))


# --------------------------------------------------------------------------
# 3. Signatures
# --------------------------------------------------------------------------

def _defaults_owner(fn: ast.FunctionDef) -> bool:
    a = fn.args
    positional = [x.arg for x in a.posonlyargs + a.args]
    pos_defaults = dict(zip(positional[len(positional) - len(a.defaults):], a.defaults))
    kw_defaults = {x.arg: d for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None}
    return "owner" in pos_defaults or "owner" in kw_defaults


def owner_modules(scoped: dict) -> list:
    """quantcore/ modules whose SQL touches a per-owner table -- derived, so a
    new repository is checked without being listed."""
    return sorted({rel for rel, _, _ in _scoped_statements(scoped) if rel.startswith("quantcore/")})


class SignatureTests(unittest.TestCase):
    def test_owner_is_never_defaulted(self):
        modules = owner_modules(owner_scoped_tables(_snapshot_tables()))
        self.assertIn("quantcore/repositories/options_position_repository.py", modules)
        defaulted = [
            f"{rel}::{fn.name}"
            for rel in modules
            for fn in ast.walk(ast.parse((REPO / rel).read_text()))
            if isinstance(fn, ast.FunctionDef) and _defaults_owner(fn)
        ]
        self.assertEqual(defaulted, [], "owner must be a required argument, never defaulted")

    def test_the_check_sees_a_default(self):
        tree = ast.parse("def a(x, owner='john'): pass\ndef b(*, owner=None): pass\n"
                         "def c(owner): pass\ndef d(*, owner): pass\ndef e(owner, y=1): pass")
        verdicts = {fn.name: _defaults_owner(fn) for fn in tree.body}
        self.assertEqual(verdicts, {"a": True, "b": True, "c": False, "d": False, "e": False})


if __name__ == "__main__":
    unittest.main()
