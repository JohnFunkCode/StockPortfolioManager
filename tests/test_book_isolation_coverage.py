"""Book isolation for every book-scoped table (#431, plan step 1.1).

The rule (CLAUDE.md, "Unified Database"): a statement that touches a
book-scoped table restricts it to one book in the SQL, and a caller that names
no book gets the owner's REAL book. That is what keeps a paper position out of
real views, alerts and CSV imports.

Like ``test_owner_isolation_coverage`` (whose tokenizer this reuses), this
keeps the rule from depending on memory. No database, no imports of the code
under test.

1. **Inventory.** Book-scoped tables are derived from the snapshot: every
   table with a ``book_id`` column (``books`` itself aside) plus every table
   with a foreign key into one, transitively. Each is in ``BOOK_SCOPED`` (naming
   its wrong-book tests, which must exist) or in ``NOT_BOOK_SCOPED`` with a
   reason; the walk doesn't continue below a table listed there.

2. **SQL scan.** Every SQL literal outside ``tests/`` that reads or writes a
   book-scoped table restricts it to a book. A *heuristic tripwire, not the
   proof* -- the behavioural tests in (1) are the proof. Accepted shapes:

   - a table with ``book_id``: at its own query level,
     ``book_id IN (SELECT ... FROM books ...)`` where that subquery binds the
     books row's ``owner`` to a parameter and picks the book by ``kind`` or by
     ``book_id`` (the real-by-default subquery every ``PortfolioRepository``
     statement carries), or ``book_id = <bound parameter>``;
   - INSERT into such a table: ``book_id`` is in the column list;
   - a child table (``lot_sales``): joined at the same level to a book-scoped
     parent that is restricted there, or filtered by ``col IN (subquery)``
     whose subquery restricts the parent;
   - an f-string hole or an ``OR`` in the WHERE clause fails.

   Reviewed exceptions go in ``ALLOWED``, keyed by file and SQL fingerprint.

3. **Signatures.** In a module whose SQL touches a book-scoped table, every
   public method that takes ``owner`` also takes a keyword-only ``book_id``
   defaulting to ``None`` -- real by default, and never positional, so an
   argument can't land in it by accident.
"""

import ast
import re
import unittest

from tests.test_owner_isolation_coverage import (
    HOLE,
    REPO,
    _IN_SUBQUERY,
    _PARAM,
    _RESTRICT,
    _TABLE_REF,
    _joined,
    _snapshot_tables,
    _source_files,
    _sql_literals,
    _test_functions,
    fingerprint,
    normalize,
    parents,
    parse_scopes,
)

# --------------------------------------------------------------------------
# 1. Inventory
# --------------------------------------------------------------------------

_REPO_TESTS = "tests/test_portfolio_repository.py"

# table -> the tests that prove a statement on it stays inside one book.
BOOK_SCOPED = {
    "positions": [
        (_REPO_TESTS, "test_default_reads_see_only_the_real_book"),
        (_REPO_TESTS, "test_a_named_book_reads_only_that_book"),
        (_REPO_TESTS, "test_default_mutations_leave_a_paper_book_alone"),
        (_REPO_TESTS, "test_another_owners_book_reads_nothing_and_takes_no_writes"),
    ],
    "lot_sales": [
        (_REPO_TESTS, "test_sales_follow_their_lots_book"),
        (_REPO_TESTS, "test_default_mutations_leave_a_paper_book_alone"),
    ],
}

NOT_BOOK_SCOPED = {
    "plan_instances": (
        "a Harvester plan is per owner and symbol, not per book. Its position_id "
        "link is optional lineage, and a plan may only exist while the owner "
        "holds the shares in the REAL book (build_plan and _close_plan_if_flat "
        "ask PortfolioRepository with no book named). plan_rungs and alerts "
        "hang off it, so the walk stops here."
    ),
}


def book_scoped_tables(tables: dict) -> dict:
    """table -> its book-scoped parents (empty for a table with ``book_id``).
    The walk doesn't descend below a NOT_BOOK_SCOPED table."""
    own = {n for n, t in tables.items() if "book_id" in t["columns"] and n != "books"}
    up = parents(tables)
    scoped = {name: set() for name in own}
    grew = True
    while grew:
        grew = False
        for name in set(tables) - set(scoped) - {"books"}:
            if name in NOT_BOOK_SCOPED:
                continue
            hit = up.get(name, set()) & set(scoped)
            if hit:
                scoped[name] = hit
                grew = True
    return scoped


def _derived(tables: dict) -> set:
    """Tables the FK walk reaches when nothing is exempt -- what must be mapped."""
    own = {n for n, t in tables.items() if "book_id" in t["columns"] and n != "books"}
    up = parents(tables)
    return own | {n for n in tables if n != "books" and up.get(n, set()) & own}


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tables = _snapshot_tables()

    def test_every_book_scoped_table_is_mapped_or_exempt(self):
        unmapped = sorted(
            (_derived(self.tables) | set(book_scoped_tables(self.tables)))
            - set(BOOK_SCOPED) - set(NOT_BOOK_SCOPED)
        )
        self.assertEqual(
            unmapped, [],
            f"book-scoped tables with no book tests on record: {unmapped}. Add the "
            "book restriction and tests, then map the table in BOOK_SCOPED (or "
            "NOT_BOOK_SCOPED with a reason).",
        )

    def test_no_stale_entries(self):
        derived = _derived(self.tables)
        stale = sorted((set(BOOK_SCOPED) | set(NOT_BOOK_SCOPED)) - derived)
        self.assertEqual(stale, [], f"mapped tables that are no longer book-scoped: {stale}")

    def test_named_tests_exist(self):
        cache = {}
        for table, tests in BOOK_SCOPED.items():
            self.assertTrue(tests, f"{table} names no book tests")
            for file, name in tests:
                funcs = cache.setdefault(file, _test_functions(REPO / file))
                with self.subTest(table=table, test=f"{file}::{name}"):
                    self.assertIn(name, funcs)

    def test_derivation(self):
        scoped = book_scoped_tables(self.tables)
        self.assertEqual(scoped["positions"], set())
        self.assertEqual(scoped["lot_sales"], {"positions"})
        self.assertNotIn("books", scoped)
        self.assertNotIn("plan_rungs", scoped)


# --------------------------------------------------------------------------
# 2. SQL scan
# --------------------------------------------------------------------------

_BOOK_EQ_PARAM = re.compile(
    r"(?:\b(\w+)\.)?\bbook_id\s*(?<![<>!])=\s*(?:cast\s*\(\s*)?" + _PARAM
)
_PICKS_BOOK = re.compile(r"\bkind\s*=\s*''|\bbook_id\s*=\s*(?:cast\s*\(\s*)?" + _PARAM)


def _names(scope, table: str) -> list:
    return [n for t, n in scope.refs() if t == table]


def _is_book_subquery(sub) -> bool:
    """A subquery over ``books`` that binds its owner and picks the book."""
    if "books" not in {t for t, _ in sub.refs()}:
        return False
    region = sub.predicate_region()
    return bool(_RESTRICT.search(region)) and bool(_PICKS_BOOK.search(region))


def _restricted_names(scope, own: set) -> set:
    """Aliases of ``book_id`` tables that this level restricts to one book."""
    refs = [(t, n) for t, n in scope.refs() if t in own]
    names = {n for _, n in refs}
    region = scope.predicate_region()
    subs = {c.id: c for c in scope.children}

    def target(qual):
        if qual is None:
            return refs[0][1] if len(refs) == 1 else None
        return qual if qual in names else None

    found = set()
    for m in _IN_SUBQUERY.finditer(region):
        sub = subs.get(int(m.group(3)))
        if m.group(2) == "book_id" and sub is not None and _is_book_subquery(sub):
            found.add(target(m.group(1)))
    for m in _BOOK_EQ_PARAM.finditer(region):
        found.add(target(m.group(1)))
    return found - {None}


def _child_ok(name: str, parent_tables: set, scope, own: set) -> bool:
    region = scope.predicate_region()
    done = _restricted_names(scope, own)
    if any(_joined(name, n, region) for t, n in scope.refs() if t in parent_tables and n in done):
        return True
    subs = {c.id: c for c in scope.children}
    single = len(scope.refs()) == 1
    for m in _IN_SUBQUERY.finditer(region):
        qual, col = m.group(1), m.group(2)
        sub = subs.get(int(m.group(3)))
        mine = qual == name if qual else (single and col != "not")
        if mine and sub is not None:
            parents_here = [n for t, n in sub.refs() if t in parent_tables]
            if parents_here and set(parents_here) <= _restricted_names(sub, own):
                return True
    return False


def _check_level(scope, scoped: dict) -> list:
    own = {t for t, anc in scoped.items() if not anc}
    named = {t for t, _ in scope.refs() if t in scoped}
    if not named:
        return []
    region = scope.predicate_region()
    if HOLE in region:
        return ["unclassifiable: an f-string hole in the WHERE clause"]
    problems = []
    if re.search(r"\bor\b", region):
        problems.append("an OR in the WHERE clause can widen the book restriction")
    insert = re.match(r"^\s*insert into (\w+)\s*\(([^)]*)\)", scope.text)
    for table in sorted(named):
        if insert and insert.group(1) == table:
            if scoped[table]:
                problems.append(f"INSERT into child table {table}: its book can't be bound here")
            elif "book_id" not in [c.strip() for c in insert.group(2).split(",")]:
                problems.append(f"INSERT into {table} without a book_id column")
            continue
        if not scoped[table]:
            if set(_names(scope, table)) - _restricted_names(scope, own):
                problems.append(f"{table} is not restricted to a book")
        elif not all(_child_ok(n, scoped[table], scope, own) for n in _names(scope, table)):
            problems.append(
                f"child table {table} not tied to a book restriction on "
                f"{', '.join(sorted(scoped[table]))}"
            )
    return problems


def check_statement(sql: str, scoped: dict) -> list:
    """Problems with one SQL statement; [] when it passes."""
    return [p for scope in parse_scopes(normalize(sql)).walk() for p in _check_level(scope, scoped)]


# (file, fingerprint) -> why the statement is safe without a book restriction.
ALLOWED = {
    # init_schema()'s all-row passes: they run with the DDL, fill a column on
    # every row whatever its book, and read nothing back.
    ("quantcore/db.py", "d984362a5e0c"): "_SCHEMA backfill: positions.status NULL -> default",
    ("quantcore/db.py", "d989b931450b"): "_SCHEMA backfill: positions.acquisition_type NULL -> default",
    ("quantcore/db.py", "76838b70090a"): "_SCHEMA backfill: positions.trade_date from purchase_date",
    ("quantcore/db.py", "c49b40516244"): "_SCHEMA backfill: creates the real books this rule relies on",
    ("quantcore/db.py", "2007bf9723aa"): "_SCHEMA backfill: this is the statement that assigns book_id",
    ("quantcore/repositories/portfolio_repository.py", "5dc86f601780"): (
        "SQL_LIST_OWNERS: restricted to real books by a join (b.kind = 'real') "
        "rather than to one book -- it is the cross-owner walk, and returns "
        "owner names only."
    ),
    ("quantcore/repositories/portfolio_repository.py", "871b12c740ba"): (
        "INSERT INTO lot_sales (SQL_INSERT_SALE): insert_sale and close_lots first "
        "read the lot with SQL_GET_LOT, owner- and book-scoped, on the same "
        "connection, and raise ValueError if it isn't in that book."
    ),
}


def _book_statements(scoped: dict):
    for path in _source_files():
        rel = path.relative_to(REPO).as_posix()
        for line, stmt in _sql_literals(path):
            if {m.group(2) for m in _TABLE_REF.finditer(normalize(stmt))} & set(scoped):
                yield rel, line, stmt


class SqlScanTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.scoped = book_scoped_tables(_snapshot_tables())

    def test_every_statement_restricts_to_a_book(self):
        failures, used, seen = [], set(), 0
        for rel, line, stmt in _book_statements(self.scoped):
            seen += 1
            problems = check_statement(stmt, self.scoped)
            if not problems:
                continue
            fp = fingerprint(stmt)
            if (rel, fp) in ALLOWED:
                used.add((rel, fp))
                continue
            failures.append(f"{rel}:{line} [{fp}] {'; '.join(problems)}\n    {normalize(stmt)[:200]}")
        self.assertEqual(
            failures, [],
            "SQL on a book-scoped table without a book restriction. Add the "
            "real-by-default subquery (see PortfolioRepository), or an ALLOWED "
            "entry with the reason:\n" + "\n".join(failures),
        )
        self.assertEqual(sorted(set(ALLOWED) - used), [], "stale ALLOWED entries")
        self.assertGreater(seen, 10, "the scan found almost no statements: it is blind")


_BOOK_SUBQUERY = (
    "(SELECT b.book_id FROM books b WHERE b.owner = :owner AND CASE WHEN "
    "CAST(:book_id AS INTEGER) IS NULL THEN b.kind = 'real' "
    "ELSE b.book_id = CAST(:book_id AS INTEGER) END)"
)
SCOPED_FIXTURE = {"positions": set(), "lot_sales": {"positions"}}

MUST_REJECT = [
    "SELECT * FROM positions WHERE owner = :owner",
    "DELETE FROM positions WHERE owner = :owner AND position_id = :lot_id",
    "UPDATE positions SET notes = :notes WHERE owner = :owner AND position_id = :lot_id",
    "INSERT INTO positions (owner, symbol_id) VALUES (:owner, :symbol_id)",
    "SELECT * FROM positions WHERE owner = :owner AND book_id IS NOT NULL",
    "SELECT * FROM positions WHERE owner = :owner AND book_id <> :book_id",
    # The subquery names books but picks no book: every book of the owner.
    "SELECT * FROM positions WHERE book_id IN (SELECT book_id FROM books WHERE owner = :owner)",
    # ... or picks a kind for every owner at once.
    "SELECT * FROM positions WHERE book_id IN (SELECT book_id FROM books WHERE kind = 'real')",
    # A subquery over some other table says nothing about the book.
    "SELECT * FROM positions WHERE book_id IN (SELECT position_id FROM lot_sales)",
    "SELECT * FROM positions WHERE owner = :owner OR book_id = :book_id",
    "SELECT * FROM positions WHERE {}",
    # Each alias is restricted on its own.
    "SELECT a.* FROM positions a JOIN positions b ON a.symbol_id = b.symbol_id "
    "WHERE a.book_id IN " + _BOOK_SUBQUERY,
    # A child must be tied to a restricted parent.
    "SELECT * FROM lot_sales WHERE sale_id = :sale_id",
    "SELECT ls.* FROM lot_sales ls JOIN positions p ON p.position_id = ls.lot_id WHERE p.owner = :owner",
    "UPDATE lot_sales SET notes = :n WHERE lot_id IN (SELECT position_id FROM positions WHERE owner = :owner)",
    "INSERT INTO lot_sales (lot_id, shares_sold) VALUES (:lot_id, :shares_sold)",
]

MUST_ACCEPT = [
    "SELECT * FROM positions p WHERE p.owner = :owner AND p.book_id IN " + _BOOK_SUBQUERY,
    "DELETE FROM positions WHERE owner = :owner AND book_id IN " + _BOOK_SUBQUERY,
    "DELETE FROM positions WHERE owner = :owner AND book_id = :book_id",
    "SELECT * FROM positions WHERE owner = %s AND book_id = %s",
    "INSERT INTO positions (owner, book_id, symbol_id) VALUES (:owner, :book_id, :symbol_id)",
    "SELECT ls.* FROM lot_sales ls JOIN positions p ON p.position_id = ls.lot_id "
    "WHERE p.owner = :owner AND p.book_id IN " + _BOOK_SUBQUERY,
    "UPDATE lot_sales SET notes = :n WHERE sale_id = :sale_id AND lot_id IN ("
    "SELECT position_id FROM positions WHERE owner = :owner AND book_id IN " + _BOOK_SUBQUERY + ")",
    "SELECT * FROM symbols WHERE ticker = :ticker",
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


# --------------------------------------------------------------------------
# 3. Signatures
# --------------------------------------------------------------------------

def _book_arg_problem(fn: ast.FunctionDef):
    """Why ``fn``'s ``book_id`` isn't keyword-only-defaulting-to-None; None when it is."""
    a = fn.args
    if "owner" not in [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]:
        return None
    if "book_id" in [x.arg for x in a.posonlyargs + a.args]:
        return "book_id must be keyword-only"
    defaults = {x.arg: d for x, d in zip(a.kwonlyargs, a.kw_defaults)}
    if "book_id" not in defaults:
        return "takes owner but no book_id"
    d = defaults["book_id"]
    if not (isinstance(d, ast.Constant) and d.value is None):
        return "book_id must default to None (the real book)"
    return None


def _public_methods(tree: ast.AST):
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_"):
                yield fn


class SignatureTests(unittest.TestCase):
    def test_public_methods_default_to_the_real_book(self):
        scoped = book_scoped_tables(_snapshot_tables())
        modules = sorted({rel for rel, _, _ in _book_statements(scoped) if rel.startswith("quantcore/repositories/")})
        self.assertIn("quantcore/repositories/portfolio_repository.py", modules)
        problems = [
            f"{rel}::{fn.name}: {problem}"
            for rel in modules
            for fn in _public_methods(ast.parse((REPO / rel).read_text()))
            for problem in [_book_arg_problem(fn)] if problem
        ]
        self.assertEqual(problems, [])

    def test_the_check_sees_each_mistake(self):
        tree = ast.parse(
            "def a(owner, *, book_id=None): pass\n"
            "def b(owner, book_id=None): pass\n"
            "def c(owner): pass\n"
            "def d(owner, *, book_id): pass\n"
            "def e(owner, *, book_id=1): pass\n"
            "def f(x): pass\n"
        )
        verdicts = {fn.name: _book_arg_problem(fn) is None for fn in tree.body}
        self.assertEqual(verdicts, {"a": True, "b": False, "c": False, "d": False, "e": False, "f": True})


if __name__ == "__main__":
    unittest.main()
