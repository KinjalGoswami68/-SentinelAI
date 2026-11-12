# database.py

import hashlib
import json
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
        # If the server dropped the connection, rollback() itself fails with
        # InterfaceError; ignore that so the original error (usually an
        # OperationalError -> HTTP 503 with Retry-After) is what propagates.
        try:
            conn.rollback()
        except Exception:
            pass
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
RLS_TABLES = ("companies", "outputs", "alerts", "schema_migrations", "investigations", "incidents", "llm_usage",
              "bot_settings", "digests", "baseline_reviews")

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
    (3, "m9: investigations", [
        """CREATE TABLE IF NOT EXISTS investigations (
               id              SERIAL PRIMARY KEY,
               company_id      INTEGER     NOT NULL REFERENCES companies(id),
               created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
               finished_at     TIMESTAMPTZ,
               status          TEXT        NOT NULL,   -- running | done | failed
               incident_start  TIMESTAMPTZ,
               cause           TEXT,
               confidence      REAL,
               written_by      TEXT,                   -- model name, or 'rules'
               fallback_reason TEXT,
               report          JSONB,
               slack_sent      BOOLEAN     NOT NULL DEFAULT false
           )""",
        """CREATE INDEX IF NOT EXISTS investigations_company_time_idx
           ON investigations (company_id, created_at)""",
        "ALTER TABLE investigations ENABLE ROW LEVEL SECURITY",
    ]),
    (4, "m6: learning state per company", [
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS learning_state   TEXT NOT NULL DEFAULT 'learning'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS ready_at         TIMESTAMPTZ",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS anchor_ids       INTEGER[]",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS threshold        REAL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS threshold_low    REAL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS threshold_high   REAL",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS state_checked_at TIMESTAMPTZ",
    ]),
    (5, "m8: incidents", [
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS is_nonanswer BOOLEAN",
        """CREATE TABLE IF NOT EXISTS incidents (
               id             SERIAL PRIMARY KEY,
               company_id     INTEGER     NOT NULL REFERENCES companies(id),
               status         TEXT        NOT NULL,
               severity       INTEGER     NOT NULL,
               opened_at      TIMESTAMPTZ NOT NULL,
               last_signal_at TIMESTAMPTZ NOT NULL,
               closed_at      TIMESTAMPTZ,
               flapping       BOOLEAN     NOT NULL DEFAULT false,
               reopen_count   INTEGER     NOT NULL DEFAULT 0,
               signal_count   INTEGER     NOT NULL DEFAULT 0,
               reasons        JSONB       NOT NULL DEFAULT '[]',
               slack_messages INTEGER     NOT NULL DEFAULT 0
           )""",
        "CREATE INDEX IF NOT EXISTS incidents_company_idx ON incidents (company_id, opened_at)",
        "ALTER TABLE incidents ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS incident_id INTEGER REFERENCES incidents(id)",
    ]),
    # Counts only, never question or answer text. company_id 0 = no company (try-it runs).
    (6, "m13: LLM usage ledger for the Groq free-tier caps", [
        """CREATE TABLE IF NOT EXISTS llm_usage (
               day        DATE    NOT NULL,
               model      TEXT    NOT NULL,
               kind       TEXT    NOT NULL,
               company_id INTEGER NOT NULL DEFAULT 0,
               units      INTEGER NOT NULL DEFAULT 0,
               requests   INTEGER NOT NULL DEFAULT 0,
               tokens     INTEGER NOT NULL DEFAULT 0,
               PRIMARY KEY (day, model, kind, company_id)
           )""",
        "ALTER TABLE llm_usage ENABLE ROW LEVEL SECURITY",
    ]),
    # Day-1 quality, spot-checks, per-bot settings, digests, baseline reviews. RAG context is
    # never stored; the Slack webhook is never returned by the API or logged.
    (7, "m14: day-1 quality, spot-checks, bot settings, digests, baseline reviews", [
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS qa_relevance REAL",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS quality_flags TEXT[]",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'live'",
        "ALTER TABLE outputs ADD COLUMN IF NOT EXISTS spot_check JSONB",
        """CREATE TABLE IF NOT EXISTS bot_settings (
               company_id    INTEGER PRIMARY KEY REFERENCES companies(id),
               slack_webhook TEXT,
               updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
           )""",
        "ALTER TABLE bot_settings ENABLE ROW LEVEL SECURITY",
        """CREATE TABLE IF NOT EXISTS digests (
               company_id INTEGER     NOT NULL REFERENCES companies(id),
               day        DATE        NOT NULL,
               body       JSONB       NOT NULL,
               slack_sent BOOLEAN     NOT NULL DEFAULT false,
               created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
               PRIMARY KEY (company_id, day)
           )""",
        "ALTER TABLE digests ENABLE ROW LEVEL SECURITY",
        """CREATE TABLE IF NOT EXISTS baseline_reviews (
               company_id INTEGER PRIMARY KEY REFERENCES companies(id),
               created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
               body       JSONB NOT NULL
           )""",
        "ALTER TABLE baseline_reviews ENABLE ROW LEVEL SECURITY",
    ]),
]


# ---------------------------------------------------------------------------
# Data functions. Each takes an open `conn` from connection() and does NOT
# commit or close it; the caller's `with connection()` block does that.
# ---------------------------------------------------------------------------

def hash_api_key(api_key):
    """SHA-256 hex of an API key. Keys are 128 random bits, so no salt is needed."""
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()


def record_llm_usage(conn, model, kind, company_id=0, units=0, requests=0, tokens=0):
    """Add to today's (UTC) counters. `units` = questions or tries asked; `requests` /
    `tokens` = LLM calls and their total tokens (Groq `usage.total_tokens`). Groq's daily
    limits reset at 00:00 UTC, so the day is the UTC date."""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO llm_usage (day, model, kind, company_id, units, requests, tokens)
        VALUES ((now() AT TIME ZONE 'utc')::date, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (day, model, kind, company_id) DO UPDATE SET
            units = llm_usage.units + EXCLUDED.units,
            requests = llm_usage.requests + EXCLUDED.requests,
            tokens = llm_usage.tokens + EXCLUDED.tokens""",
        (model, kind, int(company_id or 0), int(units), int(requests), int(tokens)))


def llm_usage_today(conn, model=None, kind=None, company_id=None):
    """Today's (UTC) totals, optionally filtered: {"units", "requests", "tokens"}."""
    where, args = ["day = (now() AT TIME ZONE 'utc')::date"], []
    for col, val in (("model", model), ("kind", kind), ("company_id", company_id)):
        if val is not None:
            where.append(f"{col} = %s"); args.append(val)
    cursor = conn.cursor()
    cursor.execute(f"""SELECT coalesce(sum(units), 0), coalesce(sum(requests), 0), coalesce(sum(tokens), 0)
                       FROM llm_usage WHERE {' AND '.join(where)}""", args)
    u, r, t = cursor.fetchone()
    return {"units": int(u), "requests": int(r), "tokens": int(t)}


def set_slack_webhook(conn, company_id, url):
    """A bot's own Slack webhook (None removes it). Never returned by the API or logged."""
    cursor = conn.cursor()
    cursor.execute("""INSERT INTO bot_settings (company_id, slack_webhook, updated_at) VALUES (%s, %s, now())
                      ON CONFLICT (company_id) DO UPDATE SET slack_webhook = EXCLUDED.slack_webhook, updated_at = now()""",
                   (int(company_id), url))


def get_slack_webhook(conn, company_id):
    cursor = conn.cursor()
    cursor.execute("SELECT slack_webhook FROM bot_settings WHERE company_id = %s", (int(company_id),))
    row = cursor.fetchone()
    return row[0] if row else None


def save_spot_check(conn, output_id, result):
    """Store a spot-check verdict on its answer (never the context it was checked against)."""
    cursor = conn.cursor()
    cursor.execute("UPDATE outputs SET spot_check = %s WHERE id = %s", (json.dumps(result), int(output_id)))


def registrations_last_24h(conn):
    """(count, oldest created_at) of companies registered in the last 24 hours.
    Used for the 50-per-day registration cap; deleted companies no longer count."""
    cursor = conn.cursor()
    cursor.execute("""SELECT count(*), min(created_at) FROM companies
                      WHERE created_at > now() - interval '24 hours'""")
    return cursor.fetchone()


def empty_companies(conn, older_than_days=7):
    """Companies with no stored answers, registered more than older_than_days ago:
    [(id, company_name, created_at)]. Candidates for the daily cleanup."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.company_name, c.created_at FROM companies c
        WHERE c.created_at < now() - make_interval(days => %s)
          AND NOT EXISTS (SELECT 1 FROM outputs o WHERE o.company_id = c.id)
        ORDER BY c.id""", (int(older_than_days),))
    return cursor.fetchall()


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
    for table in ("investigations", "incidents", "alerts", "outputs", "llm_usage",
                  "bot_settings", "digests", "baseline_reviews"):
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
         request_id, prompt_version, model_version, status, latency_ms, session_hash, is_nonanswer,
         qa_relevance, quality_flags, source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
        meta.get("is_nonanswer"),
        meta.get("qa_relevance"),
        meta.get("quality_flags"),
        meta.get("source") or "live",
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


# ---------------------------------------------------------------------------
# Investigations (M9)
# ---------------------------------------------------------------------------

INVESTIGATION_LOCK_ID = 727275   # serialises slot claims across threads/instances


def count_recent_flagged(conn, company_id, minutes=30):
    """Flagged outputs for this company in the last `minutes`."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT count(*) FROM outputs
        WHERE company_id = %s AND is_anomaly = 1
        AND   "timestamp" > now() - make_interval(mins => %s)
    """, (int(company_id), int(minutes)))
    return cursor.fetchone()[0]


def claim_investigation_slot(conn, company_id, per_company_minutes=30, daily_cap=20):
    """
    Insert a 'running' investigation if the caps allow it, else return
    (None, reason). Caps: one per company per `per_company_minutes`, and
    `daily_cap` in total over the last 24 h, so false alarms cannot use up
    the Groq free tier. Runs under an advisory lock so two threads or two
    instances cannot both claim.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (INVESTIGATION_LOCK_ID,))
    cursor.execute("""
        SELECT count(*) FROM investigations
        WHERE company_id = %s AND created_at > now() - make_interval(mins => %s)
    """, (int(company_id), int(per_company_minutes)))
    if cursor.fetchone()[0] > 0:
        return None, f"company already investigated in the last {per_company_minutes} min"
    cursor.execute("SELECT count(*) FROM investigations WHERE created_at > now() - interval '24 hours'")
    if cursor.fetchone()[0] >= daily_cap:
        return None, f"daily cap of {daily_cap} investigations reached"
    cursor.execute(
        "INSERT INTO investigations (company_id, status) VALUES (%s, 'running') RETURNING id",
        (int(company_id),),
    )
    return cursor.fetchone()[0], None


def finish_investigation(conn, investigation_id, status, report=None, incident_start=None,
                         cause=None, confidence=None, written_by=None, fallback_reason=None,
                         slack_sent=False):
    import json as _json
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE investigations
        SET status = %s, finished_at = now(), report = %s, incident_start = %s, cause = %s,
            confidence = %s, written_by = %s, fallback_reason = %s, slack_sent = %s
        WHERE id = %s
    """, (status, _json.dumps(report) if report is not None else None, incident_start, cause,
          confidence, written_by, fallback_reason, bool(slack_sent), int(investigation_id)))


def change_point(rows, min_after=3):
    """rows: [(ts, affected)] oldest first. Timestamp of the affected answer that
    maximises rate(after, inclusive) - rate(before); None if nothing is affected."""
    n = len(rows)
    total = sum(1 for _, hit in rows if hit)
    best, best_ts, before = -1.0, None, 0
    for i, (ts, hit) in enumerate(rows):
        if hit and n - i >= min_after:
            after_rate = (total - before) / (n - i)
            before_rate = before / i if i else 0.0
            if after_rate - before_rate > best:
                best, best_ts = after_rate - before_rate, ts
        before += bool(hit)
    if best_ts is None and total:
        best_ts = next(ts for ts, hit in rows if hit)
    return best_ts


_WINDOW_COLS = """id, request_id, "timestamp", prompt, response, is_anomaly, similarity, status,
                  prompt_version, model_version, latency_ms, prompt_embedding, response_embedding"""


def _window_rows(cursor):
    keys = ["id", "request_id", "ts", "prompt", "response", "is_anomaly", "similarity", "status",
            "prompt_version", "model_version", "latency_ms", "prompt_embedding", "response_embedding"]
    return [dict(zip(keys, r)) for r in cursor.fetchall()]


def fetch_window(conn, company_id, lookback_minutes=30, baseline_limit=300, incident_limit=300, start=None,
                 signal_since=None):
    """
    Data for an investigation. incident_start = first flagged row in the last
    `lookback_minutes`. Returns (incident_start, baseline_rows, incident_rows):
    baseline = up to `baseline_limit` rows before incident_start, incident =
    rows from incident_start on. Each row is a dict (see _window_rows).
    """
    cursor = conn.cursor()
    if start is None and signal_since is not None:
        # M8 incident: start where behaviour changed, not at a stray flag. Change
        # point = the affected answer (flagged or non-answer) that maximises
        # (affected rate from it on) - (affected rate before it).
        cursor.execute("""
            SELECT "timestamp", (is_anomaly = 1 OR coalesce(is_nonanswer, false)) FROM outputs
            WHERE company_id = %s AND "timestamp" >= %s ORDER BY "timestamp"
        """, (int(company_id), signal_since))
        start = change_point(cursor.fetchall())
    if start is None:   # explicit start: a version regression (first row on the new version)
        cursor.execute("""
            SELECT min("timestamp") FROM outputs
            WHERE company_id = %s AND is_anomaly = 1
            AND   "timestamp" > now() - make_interval(mins => %s)
        """, (int(company_id), int(lookback_minutes)))
        start = cursor.fetchone()[0]
    if start is None:
        return None, [], []
    cursor.execute(f"""
        SELECT {_WINDOW_COLS} FROM outputs
        WHERE company_id = %s AND "timestamp" < %s
        ORDER BY "timestamp" DESC LIMIT %s
    """, (int(company_id), start, int(baseline_limit)))
    baseline = list(reversed(_window_rows(cursor)))
    cursor.execute(f"""
        SELECT {_WINDOW_COLS} FROM outputs
        WHERE company_id = %s AND "timestamp" >= %s
        ORDER BY "timestamp" ASC LIMIT %s
    """, (int(company_id), start, int(incident_limit)))
    return start, baseline, _window_rows(cursor)


def get_past_investigations(conn, company_id, days=30, exclude_id=None):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, created_at, cause, confidence FROM investigations
        WHERE company_id = %s AND status = 'done' AND created_at > now() - make_interval(days => %s)
        AND   (%s IS NULL OR id <> %s)
        ORDER BY created_at DESC LIMIT 20
    """, (int(company_id), int(days), exclude_id, exclude_id))
    return [{"id": r[0], "created_at": r[1], "cause": r[2], "confidence": r[3]} for r in cursor.fetchall()]


VERSION_FIELDS = ("prompt_version", "model_version")   # whitelisted column names


def count_version_rows(conn, company_id, field, value):
    assert field in VERSION_FIELDS
    cursor = conn.cursor()
    cursor.execute(f"SELECT count(*) FROM outputs WHERE company_id = %s AND {field} = %s",
                   (int(company_id), value))
    return cursor.fetchone()[0]


def version_group(conn, company_id, field, value, group_limit=50, baseline_limit=300):
    """(first_seen, baseline_rows, group_rows) for a version value: baseline = rows
    before its first appearance that are not on it, group = its first rows."""
    assert field in VERSION_FIELDS
    cursor = conn.cursor()
    cursor.execute(f'SELECT min("timestamp") FROM outputs WHERE company_id = %s AND {field} = %s',
                   (int(company_id), value))
    first = cursor.fetchone()[0]
    if first is None:
        return None, [], []
    cursor.execute(f"""
        SELECT {_WINDOW_COLS} FROM outputs
        WHERE company_id = %s AND "timestamp" < %s AND {field} IS DISTINCT FROM %s
        ORDER BY "timestamp" DESC LIMIT %s
    """, (int(company_id), first, value, int(baseline_limit)))
    baseline = list(reversed(_window_rows(cursor)))
    cursor.execute(f"""
        SELECT {_WINDOW_COLS} FROM outputs
        WHERE company_id = %s AND {field} = %s
        ORDER BY "timestamp" ASC LIMIT %s
    """, (int(company_id), value, int(group_limit)))
    return first, baseline, _window_rows(cursor)


def version_alert_exists(conn, company_id, field, value):
    """Has a version_regression alert already been raised for this field/value?"""
    cursor = conn.cursor()
    cursor.execute("""SELECT 1 FROM alerts WHERE company_id = %s AND alert_type = 'version_regression'
                      AND strpos(message, %s) > 0 LIMIT 1""",
                   (int(company_id), f"[{field}={value}]"))
    return cursor.fetchone() is not None



# ---------------------------------------------------------------------------
# Learning state (M6)
# ---------------------------------------------------------------------------

def get_learning(conn, company_id):
    cursor = conn.cursor()
    cursor.execute("""SELECT learning_state, ready_at, anchor_ids, threshold, threshold_low, threshold_high
                      FROM companies WHERE id = %s""", (int(company_id),))
    r = cursor.fetchone()
    keys = ("state", "ready_at", "anchor_ids", "threshold", "threshold_low", "threshold_high")
    return dict(zip(keys, r)) if r else None


def save_learning(conn, company_id, state, threshold=None, low=None, high=None, anchor_ids=None):
    """Store the assessed state. ready_at and anchor_ids are written only on the
    first move to 'ready' (the frozen anchor never changes afterwards)."""
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE companies
        SET learning_state = %s, threshold = %s, threshold_low = %s, threshold_high = %s,
            state_checked_at = now(),
            ready_at   = CASE WHEN %s = 'ready' AND ready_at IS NULL THEN now() ELSE ready_at END,
            anchor_ids = CASE WHEN %s = 'ready' AND anchor_ids IS NULL THEN %s ELSE anchor_ids END
        WHERE id = %s
    """, (state, threshold, low, high, state, state, anchor_ids, int(company_id)))


def accepted_rows(conn, company_id, limit=500, older_than_hours=None):
    """Most recent accepted (not flagged) answers with vectors:
    (id, ts, vector, response, latency_ms)."""
    cursor = conn.cursor()
    sql = """SELECT id, "timestamp", response_embedding, response, latency_ms FROM outputs
             WHERE company_id = %s AND is_anomaly = 0 AND response_embedding IS NOT NULL"""
    args = [int(company_id)]
    if older_than_hours is not None:
        sql += """ AND "timestamp" < now() - make_interval(hours => %s)"""
        args.append(int(older_than_hours))
    sql += """ ORDER BY "timestamp" DESC LIMIT %s"""
    args.append(int(limit))
    cursor.execute(sql, args)
    return cursor.fetchall()


def rows_by_ids(conn, ids):
    if not ids:
        return []
    cursor = conn.cursor()
    cursor.execute("""SELECT id, "timestamp", response_embedding, response, latency_ms FROM outputs
                      WHERE id = ANY(%s) AND response_embedding IS NOT NULL""", (list(ids),))
    return cursor.fetchall()


def incident_open(conn, company_id, hours=2):
    """Until M8 has real incidents: an investigation in the last `hours` counts as open."""
    cursor = conn.cursor()
    cursor.execute("""SELECT 1 FROM investigations WHERE company_id = %s
                      AND created_at > now() - make_interval(hours => %s) LIMIT 1""",
                   (int(company_id), int(hours)))
    return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Incidents (M8)
# ---------------------------------------------------------------------------

_INC_COLS = ("id", "company_id", "status", "severity", "opened_at", "last_signal_at", "closed_at",
             "flapping", "reopen_count", "signal_count", "reasons", "slack_messages")


def _inc(row):
    return dict(zip(_INC_COLS, row)) if row else None


def current_incident(conn, company_id, flap_hours=2):
    """The company's open incident, or the latest one resolved within `flap_hours`
    (a new signal reopens it as flapping). None otherwise."""
    cursor = conn.cursor()
    cursor.execute(f"""SELECT {", ".join(_INC_COLS)} FROM incidents
                       WHERE company_id = %s AND (status = 'open'
                             OR closed_at > now() - make_interval(hours => %s))
                       ORDER BY opened_at DESC LIMIT 1""", (int(company_id), int(flap_hours)))
    return _inc(cursor.fetchone())


def save_incident(conn, company_id, inc):
    """Insert (id None) or update an incident dict; returns its id."""
    import json as _json
    cursor = conn.cursor()
    vals = (inc["status"], inc["severity"], inc["opened_at"], inc["last_signal_at"], inc["closed_at"],
            inc["flapping"], inc["reopen_count"], inc["signal_count"], _json.dumps(inc["reasons"]),
            inc["slack_messages"])
    if inc.get("id") is None:
        cursor.execute("""INSERT INTO incidents (status, severity, opened_at, last_signal_at, closed_at, flapping,
                          reopen_count, signal_count, reasons, slack_messages, company_id)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                       vals + (int(company_id),))
        return cursor.fetchone()[0]
    cursor.execute("""UPDATE incidents SET status=%s, severity=%s, opened_at=%s, last_signal_at=%s, closed_at=%s,
                      flapping=%s, reopen_count=%s, signal_count=%s, reasons=%s, slack_messages=%s
                      WHERE id=%s""", vals + (int(inc["id"]),))
    return inc["id"]


def bump_incident_slack(conn, incident_id):
    cursor = conn.cursor()
    cursor.execute("UPDATE incidents SET slack_messages = slack_messages + 1 WHERE id = %s RETURNING slack_messages",
                   (int(incident_id),))
    r = cursor.fetchone()
    return r[0] if r else None


def open_incidents(conn):
    cursor = conn.cursor()
    cursor.execute(f"SELECT {', '.join(_INC_COLS)} FROM incidents WHERE status = 'open'")
    return [_inc(r) for r in cursor.fetchall()]


def window_counts(conn, company_id, minutes=30, usual_days=7):
    """Counts over the last `minutes` for the incident rules, plus the company's
    usual flagged share over the previous `usual_days` days."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE is_anomaly = 1),
               count(*) FILTER (WHERE is_nonanswer),
               count(*) FILTER (WHERE lower(coalesce(status, '')) IN ('error', 'timeout', 'failed', 'failure'))
        FROM outputs WHERE company_id = %s AND "timestamp" > now() - make_interval(mins => %s)
    """, (int(company_id), int(minutes)))
    n, flagged, nonanswers, hard = cursor.fetchone()
    cursor.execute("""SELECT coalesce(quality_flags, '{}') FROM outputs
                      WHERE company_id = %s AND "timestamp" > now() - make_interval(mins => %s) AND source = 'live'""",
                   (int(company_id), int(minutes)))
    quality_window = [list(r[0]) for r in cursor.fetchall()]
    cursor.execute("""
        SELECT avg(is_anomaly)::float FROM outputs
        WHERE company_id = %s AND "timestamp" > now() - make_interval(days => %s)
        AND   "timestamp" <= now() - make_interval(mins => %s)
    """, (int(company_id), int(usual_days), int(minutes)))
    usual = cursor.fetchone()[0]
    return {"answers": n, "flagged": flagged, "nonanswers": nonanswers, "hard_failures": hard,
            "usual_flag_rate": float(usual) if usual is not None else 0.0, "quality_flags": quality_window}


def link_investigation(conn, investigation_id, incident_id, slack_sent):
    cursor = conn.cursor()
    cursor.execute("UPDATE investigations SET incident_id = %s, slack_sent = %s WHERE id = %s",
                   (int(incident_id), bool(slack_sent), int(investigation_id)))


def company_name_by_id(conn, company_id):
    cursor = conn.cursor()
    cursor.execute("SELECT company_name FROM companies WHERE id = %s", (int(company_id),))
    r = cursor.fetchone()
    return r[0] if r else f"company {company_id}"


# ---------------------------------------------------------------------------
# Read API for the UI (M8): every query is scoped to one company_id.
# ---------------------------------------------------------------------------

def list_incidents(conn, company_id, limit=50):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.id, i.status, i.severity, i.opened_at, i.closed_at, i.flapping, i.reopen_count,
               i.reasons, v.cause, v.confidence
        FROM incidents i
        LEFT JOIN LATERAL (SELECT cause, confidence FROM investigations
                           WHERE incident_id = i.id ORDER BY id DESC LIMIT 1) v ON true
        WHERE i.company_id = %s ORDER BY i.opened_at DESC LIMIT %s
    """, (int(company_id), int(limit)))
    keys = ("id", "status", "severity", "opened_at", "closed_at", "flapping", "reopen_count",
            "reasons", "cause", "confidence")
    return [dict(zip(keys, r)) for r in cursor.fetchall()]


def get_incident(conn, company_id, incident_id):
    cursor = conn.cursor()
    cursor.execute(f"SELECT {', '.join(_INC_COLS)} FROM incidents WHERE id = %s AND company_id = %s",
                   (int(incident_id), int(company_id)))
    inc = _inc(cursor.fetchone())
    if not inc:
        return None
    cursor.execute("""SELECT id, cause, confidence, written_by, fallback_reason, report, created_at
                      FROM investigations WHERE incident_id = %s ORDER BY id DESC LIMIT 1""", (inc["id"],))
    r = cursor.fetchone()
    inc["investigation"] = None if not r else dict(zip(
        ("id", "cause", "confidence", "written_by", "fallback_reason", "report", "created_at"), r))
    return inc


def distance_series(conn, company_id, start, end, limit=600):
    """Per-answer points for a chart: time, distance to normal, flags, versions."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT "timestamp", request_id, id, similarity, is_anomaly, is_nonanswer, prompt_version, model_version
        FROM outputs WHERE company_id = %s AND "timestamp" BETWEEN %s AND %s
        ORDER BY "timestamp" LIMIT %s
    """, (int(company_id), start, end, int(limit)))
    return [{"t": r[0], "request_id": r[1] or f"out-{r[2]}",
             "distance": None if r[3] is None else round(1.0 - float(r[3]), 4),
             "flagged": bool(r[4]), "nonanswer": bool(r[5]), "prompt_version": r[6], "model_version": r[7]}
            for r in cursor.fetchall()]


def version_timeline(conn, company_id, days=30):
    """First and last appearance of every prompt_version / model_version value."""
    cursor = conn.cursor()
    out = []
    for field in VERSION_FIELDS:
        cursor.execute(f"""
            SELECT {field}, min("timestamp"), max("timestamp"), count(*) FROM outputs
            WHERE company_id = %s AND {field} IS NOT NULL AND "timestamp" > now() - make_interval(days => %s)
            GROUP BY {field} ORDER BY min("timestamp")
        """, (int(company_id), int(days)))
        out += [{"field": field, "value": r[0], "first_seen": r[1], "last_seen": r[2], "answers": r[3]}
                for r in cursor.fetchall()]
    return sorted(out, key=lambda x: x["first_seen"])


def bot_counts(conn, company_id):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE "timestamp" > now() - interval '24 hours'),
               count(*) FILTER (WHERE "timestamp" > now() - interval '24 hours' AND is_anomaly = 1),
               count(*) FILTER (WHERE "timestamp" > now() - interval '24 hours' AND is_nonanswer),
               max("timestamp")
        FROM outputs WHERE company_id = %s
    """, (int(company_id),))
    total, day, flagged, nonans, last = cursor.fetchone()
    cursor.execute("SELECT count(*) FROM incidents WHERE company_id = %s AND status = 'open'", (int(company_id),))
    open_n = cursor.fetchone()[0]
    return {"answers_total": total, "answers_24h": day, "flagged_24h": flagged, "nonanswers_24h": nonans,
            "flagged_rate_24h": round(flagged / day, 4) if day else None, "last_answer_at": last,
            "open_incidents": open_n}
