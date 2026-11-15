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
from typing import Optional

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
_CASH_TESTS = "tests/test_cash_repository.py"
_ORDER_TESTS = "tests/test_orders_repository.py"

# table -> the tests that prove a statement on it stays inside one book.
BOOK_SCOPED = {
    "cash_entries": [
        (_CASH_TESTS, "test_default_reads_and_writes_stay_in_the_real_book"),
        (_CASH_TESTS, "test_a_named_book_reads_and_writes_only_that_book"),
        (_CASH_TESTS, "test_a_lot_named_in_the_wrong_book_writes_no_cash_row"),
        (_CASH_TESTS, "test_wrong_owner_mutations_change_nothing"),
    ],
    "orders": [
        (_ORDER_TESTS, "test_default_reads_see_only_the_real_book"),
        (_ORDER_TESTS, "test_a_named_book_reads_and_resolves_only_that_book"),
        (_ORDER_TESTS, "test_a_key_used_in_another_of_the_owners_books_is_refused"),
        (_ORDER_TESTS, "test_wrong_owner_mutations_change_nothing"),
    ],
    "positions": [
        (_REPO_TESTS, "test_default_reads_see_only_the_real_book"),
        (_REPO_TESTS, "test_a_named_book_reads_only_that_book"),
        (_REPO_TESTS, "test_default_mutations_leave_a_paper_book_alone"),
        (_REPO_TESTS, "test_another_owners_book_reads_nothing_and_takes_no_writes"),
        (_REPO_TESTS, "test_fx_backfill_follows_its_lots_book"),
    ],
    "lot_sales": [
        (_REPO_TESTS, "test_sales_follow_their_lots_book"),
        (_REPO_TESTS, "test_realized_sales_follow_their_lots_book"),
        (_REPO_TESTS, "test_fx_backfill_follows_its_lots_book"),
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
        # A sale reaches its book through its lot. V17's order_id makes
        # orders a second book-scoped parent, which the scan accepts in the
        # same way: a statement must tie the sale to one that is restricted.
        self.assertEqual(scoped["lot_sales"], {"positions", "orders"})
        # It has foreign keys into both, but its own book_id is the restriction.
        self.assertEqual(scoped["cash_entries"], set())
        self.assertEqual(scoped["orders"], set())
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
    """A subquery over ``books`` that binds its owner and picks the book.
    None (a subquery the level doesn't hold) is not one."""
    if sub is None or "books" not in {t for t, _ in sub.refs()}:
        return False
    region = sub.predicate_region()
    return bool(_RESTRICT.search(region)) and bool(_PICKS_BOOK.search(region))


def _alias_for(qual: Optional[str], aliases: list) -> Optional[str]:
    """The alias a ``book_id`` restriction applies to, or None when it can't
    be said. `aliases` are the level's ``book_id`` tables, one entry per
    reference. Unqualified, the column is only attributable when there is
    exactly one; qualified, the qualifier must be one of them."""
    if qual is None:
        return aliases[0] if len(aliases) == 1 else None
    return qual if qual in aliases else None


def _book_qualifiers(scope) -> list:
    """The qualifier (None when unqualified) of every ``book_id`` this level
    restricts to one book: ``book_id IN (<a book subquery>)`` and
    ``book_id = <bound parameter>``."""
    region = scope.predicate_region()
    subs = {c.id: c for c in scope.children}
    in_book = [
        m.group(1)
        for m in _IN_SUBQUERY.finditer(region)
        if m.group(2) == "book_id" and _is_book_subquery(subs.get(int(m.group(3))))
    ]
    return in_book + [m.group(1) for m in _BOOK_EQ_PARAM.finditer(region)]


def _restricted_names(scope, own: set) -> set:
    """Aliases of ``book_id`` tables that this level restricts to one book."""
    aliases = [n for t, n in scope.refs() if t in own]
    return {_alias_for(qual, aliases) for qual in _book_qualifiers(scope)} - {None}


def _joins_restricted_parent(name: str, parent_tables: set, scope, own: set) -> bool:
    """`name` is joined, at this level, to a parent this level restricts."""
    region = scope.predicate_region()
    done = _restricted_names(scope, own)
    return any(
        _joined(name, n, region) for t, n in scope.refs() if t in parent_tables and n in done
    )


def _restricts_its_parents(sub, parent_tables: set, own: set) -> bool:
    """A subquery that reads a parent table and restricts every alias of it."""
    parents_here = {n for t, n in sub.refs() if t in parent_tables}
    return bool(parents_here) and parents_here <= _restricted_names(sub, own)


def _in_subqueries_on(name: str, scope) -> list:
    """The subqueries of ``col IN (subquery)`` filters that apply to `name`:
    qualified with it, or unqualified where it is the level's only table."""
    subs = {c.id: c for c in scope.children}
    single = len(scope.refs()) == 1
    found = []
    for m in _IN_SUBQUERY.finditer(scope.predicate_region()):
        qual, col = m.group(1), m.group(2)
        mine = qual == name if qual else (single and col != "not")
        if mine:
            found.append(subs.get(int(m.group(3))))
    return [sub for sub in found if sub is not None]


def _child_ok(name: str, parent_tables: set, scope, own: set) -> bool:
    if _joins_restricted_parent(name, parent_tables, scope, own):
        return True
    return any(
        _restricts_its_parents(sub, parent_tables, own) for sub in _in_subqueries_on(name, scope)
    )


def _table_problems(tables, scope, scoped: dict) -> list:
    """What is wrong with how this level treats each of `tables`. The level's
    own INSERT target is checked as an INSERT, every other table as a read."""
    own = {t for t, anc in scoped.items() if not anc}
    insert = re.match(r"^\s*insert into (\w+)\s*\(([^)]*)\)", scope.text)
    target = insert.group(1) if insert else None
    problems = []
    for table in sorted(tables):
        if table == target:
            problem = _insert_problem(table, insert.group(2), scoped)
        else:
            problem = _read_problem(table, scope, scoped, own)
        if problem:
            problems.append(problem)
    return problems


def _check_level(scope, scoped: dict) -> list:
    named = {t for t, _ in scope.refs() if t in scoped}
    if not named:
        return []
    region = scope.predicate_region()
    if HOLE in region:
        return ["unclassifiable: an f-string hole in the WHERE clause"]
    problems = []
    if re.search(r"\bor\b", region):
        problems.append("an OR in the WHERE clause can widen the book restriction")
    return problems + _table_problems(named, scope, scoped)


def _insert_problem(table: str, columns: str, scoped: dict) -> Optional[str]:
    """What is wrong with an INSERT into `table`, or None."""
    if scoped[table]:
        return f"INSERT into child table {table}: its book can't be bound here"
    if "book_id" not in [c.strip() for c in columns.split(",")]:
        return f"INSERT into {table} without a book_id column"
    return None


def _read_problem(table: str, scope, scoped: dict, own: set) -> Optional[str]:
    """What is wrong with a statement that reads or changes `table`, or None."""
    names = _names(scope, table)
    if not scoped[table]:
        if set(names) - _restricted_names(scope, own):
            return f"{table} is not restricted to a book"
        return None
    if all(_child_ok(n, scoped[table], scope, own) for n in names):
        return None
    return (
        f"child table {table} not tied to a book restriction on "
        f"{', '.join(sorted(scoped[table]))}"
    )


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
    ("quantcore/repositories/portfolio_repository.py", "7f40e1e5c0f3"): (
        "INSERT INTO lot_sales (SQL_INSERT_SALE): insert_sale and close_lots_on first "
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


def _root(sql: str):
    return parse_scopes(normalize(sql))


_OWN = {"positions"}
_PARENTS = {"positions"}
_JOINED = (
    "SELECT ls.* FROM lot_sales ls JOIN positions p ON p.position_id = ls.lot_id "
    "WHERE p.owner = :owner"
)
_PARENT_SUBQUERY = "SELECT position_id FROM positions WHERE owner = :owner"


class ScannerHelperTests(unittest.TestCase):
    """The pieces `_child_ok` and `_check_level` were split into (plan step 2.4)."""

    def test_joins_restricted_parent(self):
        ok = _root(_JOINED + " AND p.book_id IN " + _BOOK_SUBQUERY)
        self.assertTrue(_joins_restricted_parent("ls", _PARENTS, ok, _OWN))
        # Joined, but the parent is not restricted to a book.
        self.assertFalse(_joins_restricted_parent("ls", _PARENTS, _root(_JOINED), _OWN))
        # Restricted, but nothing joins the child to it.
        loose = _root(
            "SELECT ls.* FROM lot_sales ls, positions p WHERE p.book_id IN " + _BOOK_SUBQUERY
        )
        self.assertFalse(_joins_restricted_parent("ls", _PARENTS, loose, _OWN))

    def test_restricts_its_parents(self):
        def sub(where):
            root = _root("SELECT * FROM lot_sales WHERE lot_id IN (" + where + ")")
            return root.children[0]

        self.assertTrue(
            _restricts_its_parents(
                sub(_PARENT_SUBQUERY + " AND book_id IN " + _BOOK_SUBQUERY), _PARENTS, _OWN
            )
        )
        self.assertFalse(_restricts_its_parents(sub(_PARENT_SUBQUERY), _PARENTS, _OWN))
        # A subquery that reads no parent table restricts nothing.
        self.assertFalse(
            _restricts_its_parents(sub("SELECT symbol_id FROM symbols"), _PARENTS, _OWN)
        )

    def test_in_subqueries_on(self):
        def tables(sql, name):
            return [[t for t, _ in sub.refs()] for sub in _in_subqueries_on(name, _root(sql))]

        one = "SELECT * FROM lot_sales WHERE lot_id IN (" + _PARENT_SUBQUERY + ")"
        self.assertEqual(tables(one, "lot_sales"), [["positions"]])
        # Unqualified, with two tables at the level: it can't be said whose column it is.
        two = (
            "SELECT * FROM lot_sales ls JOIN symbols s ON s.symbol_id = ls.symbol_id "
            "WHERE lot_id IN (" + _PARENT_SUBQUERY + ")"
        )
        self.assertEqual(tables(two, "ls"), [])
        qualified = two.replace("WHERE lot_id IN", "WHERE ls.lot_id IN")
        self.assertEqual(tables(qualified, "ls"), [["positions"]])
        self.assertEqual(tables(qualified, "s"), [])
        # NOT IN keeps rows outside the subquery, so it restricts nothing.
        self.assertEqual(tables(one.replace("lot_id IN", "lot_id NOT IN"), "lot_sales"), [])

    def test_alias_for(self):
        self.assertEqual(_alias_for(None, ["positions"]), "positions")
        self.assertEqual(_alias_for("p", ["p", "q"]), "p")
        # Unqualified with two book tables: it can't be said whose column it is.
        self.assertIsNone(_alias_for(None, ["p", "q"]))
        # The same table twice counts as two (INSERT ... SELECT from itself).
        self.assertIsNone(_alias_for(None, ["positions", "positions"]))
        self.assertIsNone(_alias_for(None, []))
        # A qualifier that names some other table restricts none of these.
        self.assertIsNone(_alias_for("ls", ["p"]))

    def test_book_qualifiers(self):
        def quals(where):
            return _book_qualifiers(_root("SELECT * FROM positions p WHERE " + where))

        self.assertEqual(quals("p.book_id IN " + _BOOK_SUBQUERY), ["p"])
        self.assertEqual(quals("book_id IN " + _BOOK_SUBQUERY), [None])
        self.assertEqual(quals("p.book_id = :book_id"), ["p"])
        self.assertEqual(quals("book_id = CAST(:book_id AS INTEGER)"), [None])
        # Both forms at one level: the IN ones first.
        self.assertEqual(
            quals("q.book_id = :book_id AND p.book_id IN " + _BOOK_SUBQUERY), ["p", "q"]
        )
        # Not restrictions: another column, an inequality, a subquery that
        # picks no book, and one that isn't over books at all.
        self.assertEqual(quals("p.position_id IN " + _BOOK_SUBQUERY), [])
        self.assertEqual(quals("p.book_id <> :book_id"), [])
        self.assertEqual(quals("p.book_id IS NOT NULL"), [])
        self.assertEqual(
            quals("p.book_id IN (SELECT book_id FROM books WHERE owner = :owner)"), []
        )
        self.assertEqual(quals("p.book_id IN (SELECT position_id FROM lot_sales)"), [])

    def test_restricted_names(self):
        def names(sql):
            return _restricted_names(_root(sql), _OWN)

        two = "SELECT a.* FROM positions a JOIN positions b ON a.symbol_id = b.symbol_id WHERE "
        self.assertEqual(names(two + "a.book_id IN " + _BOOK_SUBQUERY), {"a"})
        self.assertEqual(
            names(two + "a.book_id IN " + _BOOK_SUBQUERY + " AND b.book_id = :book_id"),
            {"a", "b"},
        )
        self.assertEqual(names(two + "book_id = :book_id"), set())
        self.assertEqual(names("SELECT * FROM positions WHERE book_id = :book_id"), {"positions"})
        self.assertEqual(names("SELECT * FROM positions WHERE owner = :owner"), set())
        # lot_sales has no book_id of its own, so it is never in the result.
        self.assertEqual(names("SELECT * FROM lot_sales ls WHERE ls.book_id = :book_id"), set())

    def test_is_book_subquery_takes_a_missing_subquery(self):
        self.assertFalse(_is_book_subquery(None))

    def test_table_problems(self):
        def problems(sql):
            root = _root(sql)
            named = {t for t, _ in root.refs() if t in SCOPED_FIXTURE}
            return _table_problems(named, root, SCOPED_FIXTURE)

        self.assertEqual(
            problems("INSERT INTO positions (owner, symbol_id) VALUES (:owner, :symbol_id)"),
            ["INSERT into positions without a book_id column"],
        )
        self.assertEqual(
            problems("INSERT INTO lot_sales (lot_id) VALUES (:lot_id)"),
            ["INSERT into child table lot_sales: its book can't be bound here"],
        )
        # One problem per table, in table order; the INSERT target as an
        # INSERT and the table it reads from as a read.
        self.assertEqual(
            problems(_JOINED),
            [
                "child table lot_sales not tied to a book restriction on positions",
                "positions is not restricted to a book",
            ],
        )
        self.assertEqual(problems(_JOINED + " AND p.book_id IN " + _BOOK_SUBQUERY), [])
        self.assertEqual(_table_problems(set(), _root("SELECT 1"), SCOPED_FIXTURE), [])


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
