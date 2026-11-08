# database.py

import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime

from dotenv import load_dotenv

# Load .env for local runs. load_dotenv() does NOT override variables that
# already exist, so on Render the dashboard's Environment values always win.
load_dotenv()


def get_connection(timeout=10):
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

    return psycopg2.connect(url, connect_timeout=timeout)


@contextmanager
def connection(timeout=10):
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
    """
    conn = get_connection(timeout=timeout)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ping(timeout=2):
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


def create_tables(timeout=10):

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

    print("Database tables ready.", flush=True)


# ---------------------------------------------------------------------------
# Data functions. Each takes an open `conn` from connection() and does NOT
# commit or close it; the caller's `with connection()` block does that.
# ---------------------------------------------------------------------------

def register_company(conn, company_name):
    """
    Register a new company.
    Generates a unique API key.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    api_key   = "sk-" + secrets.token_hex(16)

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO companies
        (company_name, api_key, created_at, plan)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """, (company_name.strip(), api_key, timestamp, "free"))

    row_id = cursor.fetchone()[0]

    return {
        "company_id": row_id,
        "api_key"   : api_key
    }


def get_company_by_api_key(conn, api_key):
    """
    Find company by API key.
    Used for authentication on every request.
    """
    if not api_key or not api_key.strip():
        return None

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, company_name, plan
        FROM companies
        WHERE api_key = %s
    """, (api_key.strip(),))

    row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id"          : row[0],
        "company_name": row[1],
        "plan"        : row[2]
    }


def save_output(conn, company_id, prompt, response,
                quality_score, is_anomaly, similarity=None):
    """Save one monitored AI output."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO outputs
        (company_id, prompt, response, quality_score,
         is_anomaly, similarity, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        int(company_id),
        str(prompt),
        str(response),
        float(quality_score),
        int(is_anomaly),
        float(similarity) if similarity is not None else None,
        timestamp
    ))

    return cursor.fetchone()[0]


def save_alert(conn, company_id, alert_type, message):
    """Save a fired alert."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO alerts
        (company_id, alert_type, message, timestamp)
        VALUES (%s, %s, %s, %s)
    """, (int(company_id), str(alert_type), str(message), timestamp))


def get_baseline_outputs(conn, company_id, limit=500):
    """Get company's good outputs for building ML baseline."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT response
        FROM outputs
        WHERE company_id    = %s
        AND   is_anomaly    = 0
        AND   quality_score > 6.0
        ORDER BY timestamp ASC
        LIMIT %s
    """, (int(company_id), int(limit)))

    return [row[0] for row in cursor.fetchall()]


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
        AND   quality_score > 6.0
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
        AND quality_score > 6.0
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
