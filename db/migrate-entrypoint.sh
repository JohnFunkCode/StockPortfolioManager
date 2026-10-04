#!/usr/bin/env bash
# Entrypoint of the quantcore-migrate image (issue #200): apply pending Flyway
# migrations to the database named by QUANTCORE_DB_DSN, or refuse to.
#
# Runs as the quantcore-migrate Cloud Run Job, between the image build and the
# roll-out in deploy.yml and prod-rollout.yml. A non-zero exit stops the roll-out,
# so the previous revisions keep serving on an unchanged schema.
#
#   1. Translate the libpq DSN into FLYWAY_URL / FLYWAY_USER / FLYWAY_PASSWORD.
#      Two forms are accepted:
#        postgresql://user:pw@/db?host=/cloudsql/<conn>[&port=N]   (Cloud SQL socket)
#        postgresql://user:pw@host:port/db                         (TCP, local tests)
#      JDBC has no libpq-style `host=<dir>`, so the socket form goes through the
#      junixsocket socket factory (jars in /flyway/drivers). Print only the target
#      (cloudsql:<conn>/<db>, socket:<dir>/<db> or host:port/db), never the DSN or
#      the password.
#   2. `flyway info`, then scan every *pending* migration file. Refuse (exit 3)
#      when one contains a contract statement (DROP, RENAME, ALTER ... TYPE) or a
#      statement that cannot run in a transaction (CONCURRENTLY, VACUUM,
#      ALTER SYSTEM, CREATE/DROP DATABASE). Those take the manual path:
#      ./scripts/flyway.sh [--prod] migrate. Design: docs/proposals/flyway-automation-plan.md (D4, D4a).
#   3. `flyway migrate`, then `flyway info` so the log ends with the result.
#
# The scan strips -- and /* */ comments and '...' strings before matching, so a
# keyword in a comment does not refuse. Dollar-quoted bodies are not stripped:
# a DO block executes, so a match inside one should refuse. Matching errs
# towards refusing; a false refusal costs one manual run, a false pass can
# strand the prior revision (see D4).
#
# Sourceable for tests (tests/test_migrate_entrypoint.py): main runs only when
# executed.

set -euo pipefail

FLYWAY_CONF="${FLYWAY_CONF:-db/flyway.conf}"
FLYWAY_BIN="${FLYWAY_BIN:-flyway}"

die() { echo "migrate: $*" >&2; exit 2; }

# Percent-decoding as libpq does it: %XX only, '+' stays '+'.
urldecode() {
  local s="${1//\\/\\\\}"
  printf '%b' "${s//%/\\x}"
}

# Sets FLYWAY_URL, FLYWAY_USER, FLYWAY_PASSWORD and MIGRATE_TARGET from a DSN.
parse_dsn() {
  local dsn="$1" rest userinfo hostpart path db query host="" port="" kv
  case "$dsn" in
    postgresql://*) rest="${dsn#postgresql://}" ;;
    postgres://*) rest="${dsn#postgres://}" ;;
    *) die "QUANTCORE_DB_DSN is not a postgresql:// URL" ;;
  esac
  [[ "$rest" == *@* ]] || die "QUANTCORE_DB_DSN has no user"
  # The password may itself contain '@' if unencoded: split at the last one.
  userinfo="${rest%@*}"
  rest="${rest##*@}"
  FLYWAY_USER="$(urldecode "${userinfo%%:*}")"
  if [[ "$userinfo" == *:* ]]; then
    FLYWAY_PASSWORD="$(urldecode "${userinfo#*:}")"
  else
    FLYWAY_PASSWORD=""
  fi

  query=""
  if [[ "$rest" == *\?* ]]; then
    query="${rest#*\?}"
    rest="${rest%%\?*}"
  fi
  [[ "$rest" == */* ]] || die "QUANTCORE_DB_DSN has no database name"
  hostpart="${rest%%/*}"
  path="${rest#*/}"
  db="$(urldecode "$path")"
  [[ -n "$db" ]] || die "QUANTCORE_DB_DSN has no database name"

  local IFS='&'
  for kv in $query; do
    case "$kv" in
      host=*) host="$(urldecode "${kv#host=}")" ;;
      port=*) port="${kv#port=}" ;;
    esac
  done
  unset IFS

  if [[ -z "$hostpart" ]]; then
    [[ "$host" == /* ]] || die "QUANTCORE_DB_DSN has neither a host nor a host=/socket/dir parameter"
    FLYWAY_URL="jdbc:postgresql://localhost/${db}?socketFactory=org.newsclub.net.unix.AFUNIXSocketFactory\$FactoryArg&socketFactoryArg=${host}/.s.PGSQL.${port:-5432}"
    if [[ "$host" == /cloudsql/* ]]; then
      MIGRATE_TARGET="cloudsql:${host#/cloudsql/}/${db}"
    else
      MIGRATE_TARGET="socket:${host}/${db}"
    fi
  else
    if [[ "$hostpart" != *:* && -n "$port" ]]; then hostpart="${hostpart}:${port}"; fi
    [[ "$hostpart" == *:* ]] || hostpart="${hostpart}:5432"
    FLYWAY_URL="jdbc:postgresql://${hostpart}/${db}"
    MIGRATE_TARGET="${hostpart}/${db}"
  fi
  export FLYWAY_URL FLYWAY_USER FLYWAY_PASSWORD
}

# Print one line per rule a SQL file breaks ("contract: DROP", ...); nothing if clean.
scan_sql() {
  awk '
    { text = text $0 "\n" }
    END {
      # Strip comments and single-quoted strings; keep everything else.
      n = length(text); out = ""; i = 1; depth = 0
      while (i <= n) {
        c = substr(text, i, 1); c2 = substr(text, i, 2)
        if (depth > 0) {
          if (c2 == "/*") { depth++; i += 2; continue }
          if (c2 == "*/") { depth--; i += 2; if (depth == 0) out = out " "; continue }
          i++; continue
        }
        if (c2 == "--") { while (i <= n && substr(text, i, 1) != "\n") i++; out = out " "; continue }
        if (c2 == "/*") { depth = 1; i += 2; continue }
        if (c == "\047") {
          i++
          while (i <= n) {
            if (substr(text, i, 1) == "\047") {
              if (substr(text, i + 1, 1) == "\047") { i += 2; continue }
              break
            }
            i++
          }
          i++; out = out " \047\047 "; continue
        }
        out = out c; i++
      }
      out = tolower(out)
      gsub(/[ \t\r\n]+/, " ", out)
      w = "(^|[^a-z0-9_])"; e = "([^a-z0-9_]|$)"
      k = split(out, stmts, ";")
      for (s = 1; s <= k; s++) {
        st = stmts[s]
        if (st ~ (w "drop" e)) hit["contract: DROP"] = 1
        if (st ~ (w "rename" e)) hit["contract: RENAME"] = 1
        if (st ~ (w "alter" e ".*" w "type" e)) hit["contract: ALTER ... TYPE"] = 1
        if (st ~ (w "concurrently" e)) hit["non-transactional: CONCURRENTLY"] = 1
        if (st ~ (w "vacuum" e)) hit["non-transactional: VACUUM"] = 1
        if (st ~ (w "alter system" e)) hit["non-transactional: ALTER SYSTEM"] = 1
        if (st ~ (w "(create|drop) database" e)) hit["non-transactional: CREATE/DROP DATABASE"] = 1
      }
      for (h in hit) print h
    }
  ' "$1" | sort
}

# Read `flyway info -outputType=json` on stdin; print the filepath of each Pending migration.
pending_files() {
  awk '
    /"filepath" *:/ { fp = $0; sub(/^[^:]*: *"/, "", fp); sub(/" *,? *$/, "", fp) }
    /"state" *: *"Pending"/ {
      if (fp == "") { print "migrate: a Pending migration has no filepath" > "/dev/stderr"; bad = 1 }
      else print fp
    }
    /^ *}/ { fp = "" }
    END { exit bad }
  '
}

# Scan the given files; print a refusal and return 3 if any breaks a rule.
check_pending() {
  local f hits refused=0
  for f in "$@"; do
    [[ -f "$f" ]] || die "pending migration file not found: $f"
    hits="$(scan_sql "$f")"
    if [[ -n "$hits" ]]; then
      refused=1
      while IFS= read -r h; do
        echo "migrate: REFUSED $(basename "$f"): $h"
      done <<<"$hits"
    fi
  done
  if (( refused )); then
    cat <<'EOF'
migrate: nothing was applied. Contract and non-transactional migrations are applied by hand,
migrate: with someone watching (docs/proposals/flyway-automation-plan.md, D4/D4a):
migrate:   ./scripts/flyway.sh migrate            # test
migrate:   ./scripts/flyway.sh --prod migrate     # prod
migrate: then re-run the workflow; it will find nothing pending and roll out.
EOF
    return 3
  fi
}

main() {
  [[ -n "${QUANTCORE_DB_DSN:-}" ]] || die "QUANTCORE_DB_DSN is not set"
  parse_dsn "$QUANTCORE_DB_DSN"
  echo "migrate: target ${MIGRATE_TARGET} (user: ${FLYWAY_USER})"

  local info files f pending=()
  info="$("$FLYWAY_BIN" -configFiles="$FLYWAY_CONF" info -outputType=json)"
  files="$(pending_files <<<"$info")"
  while IFS= read -r f; do
    [[ -n "$f" ]] && pending+=("$f")
  done <<<"$files"
  if (( ${#pending[@]} == 0 )); then
    echo "migrate: nothing pending"
    "$FLYWAY_BIN" -configFiles="$FLYWAY_CONF" info
    return 0
  fi
  echo "migrate: ${#pending[@]} pending: $(for f in "${pending[@]}"; do basename "$f"; done | tr '\n' ' ')"
  check_pending "${pending[@]}"

  "$FLYWAY_BIN" -configFiles="$FLYWAY_CONF" migrate
  "$FLYWAY_BIN" -configFiles="$FLYWAY_CONF" info
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
