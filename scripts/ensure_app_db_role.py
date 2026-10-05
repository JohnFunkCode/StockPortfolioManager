"""Create or repair the least-privilege application database role (issue #308).

The deployed services (api, report and news Jobs) connect as this role. It can
read and write rows -- SELECT, INSERT, UPDATE, DELETE on every table, USAGE and
SELECT on every sequence -- and nothing else: no DDL, no CREATE on the schema
or database, no role membership. Schema changes belong to the owner role
(``quantcore``), which the ``quantcore-migrate`` Job and ``scripts/flyway.sh``
use. Default privileges cover tables a later migration creates.

Run it as the owner, through the Cloud SQL Auth Proxy, with the DSNs from
``.env`` (the same mapping as ``scripts/flyway.sh``):

    python scripts/ensure_app_db_role.py              # test (default)
    python scripts/ensure_app_db_role.py --prod       # prompts for "yes"
    python scripts/ensure_app_db_role.py --dry-run    # report only, change nothing
    python scripts/ensure_app_db_role.py --keep-password   # re-grant, password unchanged

The password comes from ``QUANTCORE_APP_DB_PASSWORD`` or a prompt. It is never
sent as plaintext: the script computes a SCRAM-SHA-256 verifier locally and
sends that, so a statement log on the server can't record the password.

Building the app DSN secret without displaying it (``--swap-dsn`` reads a DSN
on stdin, writes it back with this role's user and password, and refuses to
write to a terminal):

    gcloud secrets versions access latest --secret quantcore-test-db-dsn \\
      | python scripts/ensure_app_db_role.py --swap-dsn \\
      | gcloud secrets versions add quantcore-test-db-dsn --data-file=-

The full runbook: docs/proposals/db-roles-308-plan.md.

Exit codes: 0 done and verified, 1 a problem (nothing committed if the grants
didn't verify), 2 usage.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_ROLE = "quantcore_app"
PASSWORD_ENV = "QUANTCORE_APP_DB_PASSWORD"
MIN_PASSWORD_LENGTH = 16
SCRAM_ITERATIONS = 4096  # PostgreSQL's default scram_iterations
FLYWAY_TABLE = "flyway_schema_history"
# Table privileges the role must not hold; plan_statements revokes them and
# verify() reports any that survive.
FORBIDDEN_TABLE_PRIVILEGES = ("TRUNCATE", "REFERENCES", "TRIGGER")
ROLE_FLAGS = ("SUPERUSER", "CREATEDB", "CREATEROLE", "REPLICATION", "BYPASSRLS")


def scram_verifier(password: str, *, salt: bytes | None = None,
                   iterations: int = SCRAM_ITERATIONS) -> str:
    """The SCRAM-SHA-256 verifier PostgreSQL stores in ``pg_authid.rolpassword``.

    ``CREATE ROLE ... PASSWORD 'SCRAM-SHA-256$...'`` stores it as-is, so the
    server never sees the password. (No SASLprep: the password is used as
    UTF-8 bytes, which is what libpq sends for an ASCII password.)
    """
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def swap_dsn(dsn: str, role: str, password: str) -> str:
    """``dsn`` with its user and password replaced; host, port, database and query kept.

    Works for the proxy form (``@127.0.0.1:5433/db``) and the Cloud Run socket
    form (``@/db?host=/cloudsql/...``). Both parts are percent-encoded.
    """
    parts = urlsplit(dsn.strip())
    if parts.scheme not in ("postgresql", "postgres") or "@" not in parts.netloc:
        raise ValueError("not a postgresql:// DSN with a user part")
    hostpart = parts.netloc.rsplit("@", 1)[1]
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{hostpart}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def plan_statements(role: str, database: str, *, role_exists: bool,
                    verifier: str | None, flyway_exists: bool):
    """The statements that bring ``role`` to its least-privilege shape, in order.

    Returns ``psycopg2.sql`` objects. Default privileges are set for the
    connecting role (the owner), which is who creates tables in migrations.
    """
    from psycopg2 import sql

    r, db = sql.Identifier(role), sql.Identifier(database)
    # ALTER names only what a non-superuser owner may change: in PostgreSQL 16
    # naming SUPERUSER, REPLICATION or BYPASSRLS at all (even NO...) needs a
    # superuser, and Cloud SQL's owner isn't one. verify() checks all five.
    attrs = sql.SQL("LOGIN NOCREATEDB NOCREATEROLE" if role_exists else
                    "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
    password = (sql.SQL(" PASSWORD {}").format(sql.Literal(verifier))
                if verifier else sql.SQL(""))
    verb = "ALTER ROLE {} WITH " if role_exists else "CREATE ROLE {} WITH "
    stmts = [sql.SQL(verb).format(r) + attrs + password]
    stmts += [
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, r),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(r),
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(r),
        sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(r),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(r),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT USAGE, SELECT ON SEQUENCES TO {}").format(r),
        # Repair: a grant made before this script ran (by hand, or an older
        # version) survives the GRANTs above. REVOKE of a privilege the role
        # doesn't hold is a no-op, so these are safe on every run.
        sql.SQL("REVOKE {} ON ALL TABLES IN SCHEMA public FROM {}")
        .format(sql.SQL(", ").join(sql.SQL(p) for p in FORBIDDEN_TABLE_PRIVILEGES), r),
        sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(r),
        sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(db, r),
    ]
    if flyway_exists:
        # The app reads the ledger (QUANTCORE_SCHEMA_MODE=auto) but never writes it.
        stmts.append(sql.SQL("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON {} FROM {}")
                     .format(sql.Identifier(FLYWAY_TABLE), r))
    return stmts


def verify(cur, role: str, database: str) -> list[str]:
    """Problems with ``role``'s privileges, as human-readable lines; empty means good."""
    cur.execute(
        "SELECT oid, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
        "rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s", (role,))
    row = cur.fetchone()
    if row is None:
        return [f"role {role} does not exist"]
    oid, *flags, can_login = row
    return (_attribute_problems(flags, can_login)
            + _rights_problems(cur, oid, role, database)
            + _table_problems(cur, role)
            + _sequence_problems(cur, role))


def _attribute_problems(flags, can_login: bool) -> list[str]:
    """Role attributes: none of ROLE_FLAGS, and LOGIN."""
    problems = [f"role has {n}" for n, on in zip(ROLE_FLAGS, flags) if on]
    if not can_login:
        problems.append("role cannot LOGIN")
    return problems


def _rights_problems(cur, oid: int, role: str, database: str) -> list[str]:
    """Rights outside the tables: role membership, CREATE on the schema or database."""
    problems = []
    cur.execute("SELECT count(*) FROM pg_auth_members WHERE member = %s", (oid,))
    if cur.fetchone()[0]:
        problems.append("role is a member of another role (it would inherit its rights)")
    cur.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE'), "
                "has_database_privilege(%s, %s, 'CREATE')", (role, role, database))
    schema_create, db_create = cur.fetchone()
    if schema_create:
        problems.append("role can CREATE in schema public (DDL)")
    if db_create:
        problems.append(f"role can CREATE in database {database}")
    return problems


def _table_problems(cur, role: str) -> list[str]:
    """Every table: SELECT; DML except on the ledger; none of the forbidden privileges."""
    cur.execute(
        """
        SELECT c.relname,
               has_table_privilege(%(r)s, c.oid, 'SELECT'),
               has_table_privilege(%(r)s, c.oid, 'INSERT, UPDATE, DELETE'),
               ARRAY(SELECT p FROM unnest(%(forbidden)s::text[]) AS p
                     WHERE has_table_privilege(%(r)s, c.oid, p))
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
        """, {"r": role, "forbidden": list(FORBIDDEN_TABLE_PRIVILEGES)})
    problems = []
    for name, can_select, can_write, excess in cur.fetchall():
        problems += _one_table_problems(name, can_select, can_write, excess)
    return problems


def _one_table_problems(name: str, can_select: bool, can_write: bool,
                        excess: list[str]) -> list[str]:
    problems = [] if can_select else [f"no SELECT on {name}"]
    if name == FLYWAY_TABLE and can_write:
        problems.append(f"can write {name} (the migration ledger)")
    elif name != FLYWAY_TABLE and not can_write:
        problems.append(f"no INSERT/UPDATE/DELETE on {name}")
    problems += [f"has {p} on {name}" for p in excess]
    return problems


def _sequence_problems(cur, role: str) -> list[str]:
    # The CASE is load-bearing: PostgreSQL may evaluate WHERE conditions in any
    # order, and has_sequence_privilege() raises on a table ("watchlist" is not
    # a sequence -- seen on Cloud SQL test). Only CASE guarantees the order.
    cur.execute(
        """
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND CASE WHEN c.relkind = 'S'
                   THEN NOT has_sequence_privilege(%s, c.oid, 'USAGE')
                   ELSE false END
        ORDER BY c.relname
        """, (role,))
    return [f"no USAGE on sequence {name}" for (name,) in cur.fetchall()]


def login_probe(app_dsn: str) -> list[str]:
    """Log in as the app role: reading must work and DDL must be refused."""
    import psycopg2
    import psycopg2.errors

    problems = []
    conn = psycopg2.connect(app_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
            cur.fetchone()
            try:
                cur.execute("CREATE TABLE quantcore_app_ddl_probe (x int)")
                problems.append("login probe: CREATE TABLE succeeded as the app role")
            except psycopg2.errors.InsufficientPrivilege:
                pass
    finally:
        conn.rollback()
        conn.close()
    return problems


def _target_dsn(target: str) -> str:
    import quantcore.db  # noqa: F401  (import side effect: load_dotenv from .env)

    var = "QUANTCORE_DB_DSN" if target == "prod" else "QUANTCORE_TEST_DB_DSN"
    dsn = os.environ.get(var)
    if not dsn:
        raise SystemExit(f"ERROR: {var} not found in .env")
    return dsn


def _read_password(*, confirm: bool) -> str:
    password = os.environ.get(PASSWORD_ENV)
    if password is None:
        password = getpass.getpass(f"Password for the app role (or set {PASSWORD_ENV}): ")
        if confirm and getpass.getpass("Again: ") != password:
            raise SystemExit("ERROR: the passwords differ")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"ERROR: the password must be at least {MIN_PASSWORD_LENGTH} characters")
    return password


def _swap_mode(role: str) -> int:
    if sys.stdout.isatty():
        print("ERROR: --swap-dsn writes a credential; pipe it into "
              "`gcloud secrets versions add`, not a terminal", file=sys.stderr)
        return 2
    dsn = sys.stdin.read()
    password = _read_password(confirm=False)
    try:
        sys.stdout.write(swap_dsn(dsn, role, password))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--test", action="store_const", dest="target", const="test")
    where.add_argument("--prod", action="store_const", dest="target", const="prod")
    parser.add_argument("--role", default=DEFAULT_ROLE)
    parser.add_argument("--keep-password", action="store_true",
                        help="re-apply the grants without changing an existing role's password")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the role's current state; change nothing")
    parser.add_argument("--swap-dsn", action="store_true",
                        help="DSN on stdin -> same DSN as this role on stdout; no database access")
    args = parser.parse_args(argv)
    args.target = args.target or "test"
    return args


def _print_problems(problems: list[str]) -> None:
    for line in problems:
        print(f"  PROBLEM {line}")


def _inspect(cur, role: str) -> tuple[bool, bool]:
    """(the role exists, the Flyway ledger exists)."""
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    role_exists = cur.fetchone() is not None
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{FLYWAY_TABLE}",))
    return role_exists, bool(cur.fetchone()[0])


def _dry_run(cur, role: str, database: str, role_exists: bool) -> int:
    problems = verify(cur, role, database)
    print("role exists" if role_exists else "role does not exist yet")
    _print_problems(problems)
    print("dry run: nothing changed")
    return 1 if problems else 0


def _choose_password(args, role_exists: bool) -> tuple[int, str | None]:
    """(exit code, password). A nonzero code means stop; None means keep the current one."""
    if args.target == "prod" and input("Apply to PROD? type yes: ").strip() != "yes":
        print("aborted")
        return 1, None
    if args.keep_password:
        if not role_exists:
            print("ERROR: --keep-password needs an existing role", file=sys.stderr)
            return 1, None
        return 0, None
    return 0, _read_password(confirm=True)


def _apply(conn, role: str, database: str, *, role_exists: bool, flyway_exists: bool,
           password: str | None) -> int:
    """Run the plan and verify it in one transaction; commit only if it verifies."""
    verifier = scram_verifier(password) if password else None
    with conn.cursor() as cur:
        for stmt in plan_statements(role, database, role_exists=role_exists,
                                    verifier=verifier, flyway_exists=flyway_exists):
            cur.execute(stmt)
        problems = verify(cur, role, database)
    if problems:
        conn.rollback()
        _print_problems(problems)
        print("rolled back: nothing changed")
        return 1
    conn.commit()
    print(f"{'updated' if role_exists else 'created'} {role}: DML only, verified")
    return 0


def _probe(dsn: str, role: str, password: str | None) -> int:
    if password is None:
        print("login probe skipped (--keep-password)")
        return 0
    problems = login_probe(swap_dsn(dsn, role, password))
    _print_problems(problems)
    if problems:
        return 1
    print("login probe: SELECT works, CREATE TABLE refused")
    return 0


def _run(conn, args, database: str) -> tuple[int, str | None]:
    """Everything that needs the owner's connection. (exit code, password used)."""
    with conn.cursor() as cur:
        role_exists, flyway_exists = _inspect(cur, args.role)
        if args.dry_run:
            return _dry_run(cur, args.role, database, role_exists), None
    rc, password = _choose_password(args, role_exists)
    if rc:
        return rc, None
    rc = _apply(conn, args.role, database, role_exists=role_exists,
                flyway_exists=flyway_exists, password=password)
    return rc, password


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if args.swap_dsn:
        return _swap_mode(args.role)

    import psycopg2
    from quantcore.db import describe_dsn

    dsn = _target_dsn(args.target)
    parts = urlsplit(dsn)
    database = parts.path.lstrip("/")
    print(f"target: {args.target}  {describe_dsn(dsn)}  (as: {parts.username}, role: {args.role})")
    if parts.username == args.role:
        print("ERROR: .env's DSN already logs in as the app role; this must run as the owner",
              file=sys.stderr)
        return 1

    conn = psycopg2.connect(dsn)
    try:
        rc, password = _run(conn, args, database)
    finally:
        conn.close()
    if rc or args.dry_run:
        return rc
    return _probe(dsn, args.role, password)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
