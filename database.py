# database.py

import hashlib
import os
import re
import secrets
import time
import tempfile
from contextlib import contextmanager

from dotenv import load_dotenv

# Load .env for local runs. load_dotenv() does NOT override variables that
# already exist, so on Render the dashboard's Environment values always win.
load_dotenv()

# Seconds allowed for one connect to the Supabase pooler. Measured from the
# laptop (India -> Singapore, ~143 ms RTT): a connect is ~8 round trips,
# median ~1.1 s, worst seen 4.6 s. 15 s leaves room for spikes while still
# failing long before the OS TCP timeout (minutes).
CONNECT_TIMEOUT = 15

# A connect can fail transiently (seen locally: "SSL error: unexpected eof
# while reading", "server closed the connection unexpectedly"). Retrying the
# CONNECT is safe because nothing has run yet. We never retry a transaction.
CONNECT_ATTEMPTS    = 2
CONNECT_RETRY_DELAY = 0.5   # seconds


def get_connection(timeout=CONNECT_TIMEOUT):
    """
    Open a new Postgres connection.

    DATABASE_URL is read here, at call time, not at import time, so a value
    loaded or changed after this module was imported is still picked up.
    `timeout` (seconds) bounds the connect; without it libpq can wait on the
    OS TCP timeout, which is minutes.
    """
    import psycopg2

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise ValueError(
            "DATABASE_URL environment variable not set. "
            "Add it in Render Environment tab or in a local .env file."
        )

    return psycopg2.connect(url, connect_timeout=timeout, **ssl_settings())


_ca_path_cache = None


def ssl_settings():
    """
    TLS for Postgres (decided in M4, see CLAUDE.md):
      - verify-full when Supabase's CA certificate is supplied, either as PEM
        text in DATABASE_CA_CERT or as a file path in DATABASE_CA_CERT_FILE
        (e.g. a Render Secret File). Encrypts and checks certificate + host name.
      - otherwise require: always encrypted, certificate not checked.
    These override any sslmode inside DATABASE_URL.
    """
    global _ca_path_cache
    path = os.environ.get("DATABASE_CA_CERT_FILE", "").strip()
    pem  = os.environ.get("DATABASE_CA_CERT", "").strip()
    if not path and pem:
        if _ca_path_cache is None:
            f = tempfile.NamedTemporaryFile("w", suffix=".crt", delete=False)
            f.write(pem.replace("\\n", "\n") + "\n")   # tolerate a one-line value with \n
            f.close()
            _ca_path_cache = f.name
        path = _ca_path_cache
    if path:
        return {"sslmode": "verify-full", "sslrootcert": path}
    return {"sslmode": "require"}


def ssl_mode():
    return ssl_settings()["sslmode"]


def _connect_with_retry(timeout):
    """get_connection(), retried once on OperationalError after a short pause."""
    import psycopg2

    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return get_connection(timeout=timeout)
        except psycopg2.OperationalError as e:
            if attempt == CONNECT_ATTEMPTS:
                raise
            print(f"DB connect attempt {attempt} failed, retrying: {safe_error(e)}", flush=True)
            time.sleep(CONNECT_RETRY_DELAY)


@contextmanager
def connection(timeout=CONNECT_TIMEOUT):
    """
    One connection for one unit of work (normally: one HTTP request).

        with connection() as conn:
            company = get_company_by_api_key(conn, key)
            save_output(conn, ...)
            save_alert(conn, ...)
        # <- committed and closed here

    Every query in the block shares the same connection, so a request pays
    for one TCP+TLS handshake to the pooler instead of one per query.
    The block is one transaction: it commits only if the block finishes
    without an exception, otherwise everything in it is rolled back (no
    output saved without its alert). The connection is always closed.
    Do slow non-DB work (Slack, HTTP calls) AFTER the block, not inside it.
    Only the connect is retried (once); the block itself never is.
    """
    conn = _connect_with_retry(timeout)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping(timeout=CONNECT_TIMEOUT):
    """Cheapest possible liveness check: connect, SELECT 1, close."""
    with connection(timeout=timeout) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()


_URI_RE      = re.compile(r"postgres(?:ql)?://\S+", re.IGNORECASE)
_PASSWORD_RE = re.compile(r"password\s*=\s*\S+", re.IGNORECASE)


def safe_error(exc):
    """
    One-line description of a DB exception that is safe to log.
    Keeps the class name and the first line of the message, and redacts
    anything that looks like a connection URI or a password=... pair.
    """
    first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    first_line = _URI_RE.sub("<redacted-uri>", first_line)
    first_line = _PASSWORD_RE.sub("password=<redacted>", first_line)
    return f"{type(exc).__name__}: {first_line}"


def create_tables(timeout=CONNECT_TIMEOUT):

    with connection(timeout=timeout) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id           SERIAL PRIMARY KEY,
                company_name TEXT    NOT NULL,
                api_key      TEXT    NOT NULL UNIQUE,
                created_at   TEXT    NOT NULL,
                plan         TEXT    NOT NULL DEFAULT 'free'
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS outputs (
                id            SERIAL PRIMARY KEY,
                company_id    INTEGER NOT NULL,
                prompt        TEXT    NOT NULL,
                response      TEXT    NOT NULL,
                quality_score REAL    NOT NULL,
                is_anomaly    INTEGER NOT NULL DEFAULT 0,
                similarity    REAL,
                timestamp     TEXT    NOT NULL,
                FOREIGN KEY   (company_id) REFERENCES companies(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id         SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                alert_type TEXT    NOT NULL,
                message    TEXT    NOT NULL,
                timestamp  TEXT    NOT NULL,
                FOREIGN KEY (company_id) REFERENCES companies(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT        NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

    # Each migration runs in its own transaction, under an advisory lock so two
    # instances starting at once (old + new during a Render deploy) never both
    # apply it. Applied versions are recorded, so each runs exactly once.
    for version, name, statements in MIGRATIONS:
        with connection(timeout=timeout) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
            cursor.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (version,))
            if cursor.fetchone():
                continue
            for sql in statements:
                cursor.execute(sql)
            cursor.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)", (version, name)
            )
            print(f"Applied migration {version}: {name}", flush=True)

    with connection(timeout=timeout) as conn:
        cursor = conn.cursor()
        # Row Level Security with no policies: Supabase's public API roles
        # (anon, authenticated via PostgREST) can read nothing. The app connects
        # as `postgres`, the table owner with BYPASSRLS, so it is unaffected.
        # Only ALTER tables where RLS is off: ALTER takes an exclusive lock.
        cursor.execute("""
            SELECT c.relname
            FROM   pg_class c
            JOIN   pg_namespace n ON n.oid = c.relnamespace
            WHERE  n.nspname = 'public'
            AND    c.relname = ANY(%s)
            AND    NOT c.relrowsecurity
        """, (list(RLS_TABLES),))
        for (table,) in cursor.fetchall():
            cursor.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
            print(f"Row Level Security enabled on {table}.", flush=True)

    print("Database tables ready.", flush=True)


# Every table in the public schema; each must have RLS on (CLAUDE.md).
RLS_TABLES = ("companies", "outputs", "alerts", "schema_migrations")

MIGRATION_LOCK_ID = 727274   # arbitrary constant for pg_advisory_xact_lock

# (version, name, [SQL statements]). Append only; never edit an applied one.
# Every statement must also work on a fresh database created by create_tables().
MIGRATIONS = [
    (1, "m3: stored prompt/response embeddings", [
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS prompt_embedding   REAL[]",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS response_embedding REAL[]",
    ]),
    (2, "m4: schema v2 (optional metadata, timestamptz, hashed API keys)", [
        # Optional client metadata. NULL means unknown.
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS request_id     TEXT",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS prompt_version TEXT",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS model_version  TEXT",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS status         TEXT",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS latency_ms     INTEGER",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS session_hash   TEXT",
        # quality_score becomes optional and display-only.
        "ALTER TABLE outputs ALTER COLUMN quality_score DROP NOT NULL",
        # Server-set UTC timestamps. Old text values were written by servers
        # whose clock was UTC (Render, Supabase); read them as UTC.
        """ALTER TABLE outputs ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ
           USING ("timestamp"::timestamp AT TIME ZONE 'UTC')""",
        """ALTER TABLE outputs ALTER COLUMN "timestamp" SET DEFAULT now()""",
        """ALTER TABLE alerts ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ
           USING ("timestamp"::timestamp AT TIME ZONE 'UTC')""",
        """ALTER TABLE alerts ALTER COLUMN "timestamp" SET DEFAULT now()""",
        """ALTER TABLE companies ALTER COLUMN created_at TYPE TIMESTAMPTZ
           USING (created_at::timestamp AT TIME ZONE 'UTC')""",
        "ALTER TABLE companies ALTER COLUMN created_at SET DEFAULT now()",
        # API keys: keep only SHA-256 + a 6-char prefix for display.
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS api_key_hash   TEXT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS api_key_prefix TEXT",
        """UPDATE companies
           SET    api_key_hash = encode(sha256(convert_to(api_key, 'UTF8')), 'hex'),
                  api_key_prefix = left(api_key, 6)
           WHERE  api_key IS NOT NULL AND api_key_hash IS NULL""",
        "ALTER TABLE companies ALTER COLUMN api_key DROP NOT NULL",
        "UPDATE companies SET api_key = NULL WHERE api_key_hash IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS companies_api_key_hash_key ON companies (api_key_hash)",
        # Idempotent ingest: a client retry with the same request_id is a no-op.
        """CREATE UNIQUE INDEX IF NOT EXISTS outputs_company_request_key
           ON outputs (company_id, request_id) WHERE request_id IS NOT NULL""",
        """CREATE INDEX IF NOT EXISTS outputs_company_time_idx
           ON outputs (company_id, "timestamp")""",
    ]),
]


# ---------------------------------------------------------------------------
# Data functions. Each takes an open `conn` from connection() and does NOT
# commit or close it; the caller's `with connection()` block does that.
# ---------------------------------------------------------------------------

def hash_api_key(api_key):
    """SHA-256 hex of an API key. Keys are 128 random bits, so no salt is needed."""
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()


def register_company(conn, company_name):
    """
    Register a new company. The API key is returned once and only its hash and
    a 6-character prefix are stored, so it cannot be recovered later.
    """
    api_key = "sk-" + secrets.token_hex(16)

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO companies
        (company_name, api_key_hash, api_key_prefix, plan)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """, (company_name.strip(), hash_api_key(api_key), api_key[:6], "free"))

    row_id = cursor.fetchone()[0]

    return {
        "company_id": row_id,
        "api_key"   : api_key
    }


def get_company_by_api_key(conn, api_key):
    """
    Find the company for an API key (by its SHA-256). Used on every request.

    Fallback: a key stored in plain text (registered by an older server version
    during a deploy overlap) is matched once, then hashed and cleared.
    """
    if not api_key or not api_key.strip():
        return None

    key_hash = hash_api_key(api_key)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, company_name, plan
        FROM companies
        WHERE api_key_hash = %s
    """, (key_hash,))
    row = cursor.fetchone()

    if row is None:
        cursor.execute("""
            UPDATE companies
            SET    api_key_hash = %s, api_key_prefix = left(api_key, 6), api_key = NULL
            WHERE  api_key = %s AND api_key_hash IS NULL
            RETURNING id, company_name, plan
        """, (key_hash, api_key.strip()))
        row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id"          : row[0],
        "company_name": row[1],
        "plan"        : row[2]
    }


def delete_company(conn, company_id):
    """Delete a company and all its outputs and alerts. Returns row counts."""
    cursor = conn.cursor()
    counts = {}
    for table in ("alerts", "outputs"):
        cursor.execute(f"DELETE FROM {table} WHERE company_id = %s", (int(company_id),))
        counts[table] = cursor.rowcount
    cursor.execute("DELETE FROM companies WHERE id = %s", (int(company_id),))
    counts["companies"] = cursor.rowcount
    return counts


def _vec(v):
    """numpy vector -> list of Python floats for a REAL[] column (None stays None)."""
    return None if v is None else [float(x) for x in v]


def save_output(conn, company_id, prompt, response,
                quality_score, is_anomaly, similarity=None,
                prompt_embedding=None, response_embedding=None, meta=None):
    """
    Save one monitored AI output. The timestamp is set by the database (UTC).
    `meta`: optional request_id, prompt_version, model_version, status,
    latency_ms, session_hash. Returns the new id, or None if this company
    already has a row with the same request_id (a client retry).
    """
    meta = meta or {}
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO outputs
        (company_id, prompt, response, quality_score,
         is_anomaly, similarity,
         prompt_embedding, response_embedding,
         request_id, prompt_version, model_version, status, latency_ms, session_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, request_id) WHERE request_id IS NOT NULL DO NOTHING
        RETURNING id
    """, (
        int(company_id),
        str(prompt),
        str(response),
        float(quality_score) if quality_score is not None else None,
        int(is_anomaly),
        float(similarity) if similarity is not None else None,
        _vec(prompt_embedding),
        _vec(response_embedding),
        meta.get("request_id"),
        meta.get("prompt_version"),
        meta.get("model_version"),
        meta.get("status"),
        meta.get("latency_ms"),
        meta.get("session_hash"),
    ))

    row = cursor.fetchone()
    return row[0] if row else None


def output_exists(conn, company_id, request_id):
    """True if this company already stored a row with this request_id."""
    if not request_id:
        return False
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM outputs WHERE company_id = %s AND request_id = %s",
        (int(company_id), request_id),
    )
    return cursor.fetchone() is not None


def get_rows_missing_embeddings(conn, limit=200, company_id=None):
    """(id, prompt, response) of rows that have no stored embeddings yet."""
    cursor = conn.cursor()
    sql = """
        SELECT id, prompt, response FROM outputs
        WHERE  (prompt_embedding IS NULL OR response_embedding IS NULL)
    """
    args = []
    if company_id is not None:
        sql += " AND company_id = %s"
        args.append(int(company_id))
    sql += " ORDER BY id LIMIT %s"
    args.append(int(limit))
    cursor.execute(sql, args)
    return cursor.fetchall()


def set_embeddings(conn, rows):
    """rows: iterable of (output_id, prompt_vector, response_vector)."""
    cursor = conn.cursor()
    for output_id, pv, rv in rows:
        cursor.execute(
            "UPDATE outputs SET prompt_embedding = %s, response_embedding = %s WHERE id = %s",
            (_vec(pv), _vec(rv), int(output_id)),
        )


def save_alert(conn, company_id, alert_type, message):
    """Save a fired alert (timestamp set by the database, UTC). Returns its id."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO alerts
        (company_id, alert_type, message)
        VALUES (%s, %s, %s)
        RETURNING id
    """, (int(company_id), str(alert_type), str(message)))
    return cursor.fetchone()[0]


def get_baseline_outputs(conn, company_id, limit=500):
    """Company's good outputs for the baseline: list of (id, response, response_embedding).

    response_embedding is a list of floats, or None for rows not yet backfilled.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, response, response_embedding
        FROM outputs
        WHERE company_id    = %s
        AND   is_anomaly    = 0
        AND   (quality_score IS NULL OR quality_score > 6.0)
        ORDER BY timestamp ASC
        LIMIT %s
    """, (int(company_id), int(limit)))

    return cursor.fetchall()


def get_output_count(conn, company_id):
    """Get total number of outputs for a company."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM outputs WHERE company_id = %s",
        (int(company_id),)
    )
    return cursor.fetchone()[0]


def get_good_output_count(conn, company_id):
    """Get count of good quality outputs for warmup check."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*)
        FROM outputs
        WHERE company_id    = %s
        AND   is_anomaly    = 0
        AND   (quality_score IS NULL OR quality_score > 6.0)
    """, (int(company_id),))
    return cursor.fetchone()[0]


def get_company_outputs(conn, company_id, limit=100):
    """Get recent outputs for a company."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, prompt, response, quality_score,
               is_anomaly, similarity, timestamp
        FROM outputs
        WHERE company_id = %s
        ORDER BY timestamp DESC
        LIMIT %s
    """, (int(company_id), int(limit)))

    return cursor.fetchall()


def get_company_alerts(conn, company_id, limit=50):
    """Get recent alerts for a company."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, alert_type, message, timestamp
        FROM alerts
        WHERE company_id = %s
        ORDER BY timestamp DESC
        LIMIT %s
    """, (int(company_id), int(limit)))

    return cursor.fetchall()


def get_recent_scores(conn, company_id, limit=50):
    """Get recent quality scores oldest first."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT quality_score
        FROM outputs
        WHERE company_id = %s
        AND   quality_score IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT %s
    """, (int(company_id), int(limit)))

    scores = [float(row[0]) for row in cursor.fetchall()]
    scores.reverse()
    return scores


def get_company_stats(conn, company_id):
    """Get summary statistics for a company."""
    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM outputs WHERE company_id = %s",
        (int(company_id),)
    )
    total = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM outputs WHERE company_id = %s AND is_anomaly = 1",
        (int(company_id),)
    )
    anomalies = cursor.fetchone()[0]

    cursor.execute(
        "SELECT AVG(quality_score) FROM outputs WHERE company_id = %s",
        (int(company_id),)
    )
    avg_raw   = cursor.fetchone()[0]
    avg_score = round(float(avg_raw), 2) if avg_raw is not None else 0.0

    cursor.execute(
        "SELECT COUNT(*) FROM alerts WHERE company_id = %s",
        (int(company_id),)
    )
    alert_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM outputs
        WHERE company_id = %s
        AND is_anomaly = 0
        AND (quality_score IS NULL OR quality_score > 6.0)
    """, (int(company_id),))
    good_count = cursor.fetchone()[0]

    return {
        "total_outputs"  : total,
        "total_anomalies": anomalies,
        "avg_score"      : avg_score,
        "total_alerts"   : alert_count,
        "good_outputs"   : good_count
    }


def get_all_companies(conn):
    """Get all registered companies."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, company_name, created_at, plan
        FROM companies
        ORDER BY created_at DESC
    """)
    return cursor.fetchall()
