# database.py

import os
import secrets
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_connection():
    import psycopg2
    
    if not DATABASE_URL:
        raise ValueError(
            "DATABASE_URL environment variable not set. "
            "Add it in Render Environment tab."
        )
    
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def create_tables():

    conn = get_connection()
    try:
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

        conn.commit()
        print("Database tables ready.")

    finally:
        conn.close()


def register_company(company_name):
    """
    Register a new company.
    Generates a unique API key.
    Data stored permanently in Supabase.
    """
    conn      = get_connection()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    api_key   = "sk-" + secrets.token_hex(16)

    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO companies
            (company_name, api_key, created_at, plan)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (company_name.strip(), api_key, timestamp, "free"))

        row_id = cursor.fetchone()[0]
        conn.commit()

        return {
            "company_id": row_id,
            "api_key"   : api_key
        }

    finally:
        conn.close()


def get_company_by_api_key(api_key):
    """
    Find company by API key.
    Used for authentication on every request.
    """
    if not api_key or not api_key.strip():
        return None

    conn = get_connection()
    try:
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

    finally:
        conn.close()


def save_output(company_id, prompt, response,
                quality_score, is_anomaly, similarity=None):
    """
    Save one monitored AI output to database.
    """
    conn      = get_connection()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
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

        row_id = cursor.fetchone()[0]
        conn.commit()
        return row_id

    finally:
        conn.close()


def save_alert(company_id, alert_type, message):
    """
    Save a fired alert to database.
    """
    conn      = get_connection()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO alerts
            (company_id, alert_type, message, timestamp)
            VALUES (%s, %s, %s, %s)
        """, (int(company_id), str(alert_type), str(message), timestamp))

        conn.commit()

    finally:
        conn.close()


def get_baseline_outputs(company_id, limit=500):
    """
    Get company's good outputs for building ML baseline.
    """
    conn = get_connection()
    try:
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

        rows = cursor.fetchall()
        return [row[0] for row in rows]

    finally:
        conn.close()


def get_output_count(company_id):
    """Get total number of outputs for a company."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM outputs WHERE company_id = %s",
            (int(company_id),)
        )
        return cursor.fetchone()[0]

    finally:
        conn.close()


def get_good_output_count(company_id):
    """Get count of good quality outputs for warmup check."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*)
            FROM outputs
            WHERE company_id    = %s
            AND   is_anomaly    = 0
            AND   quality_score > 6.0
        """, (int(company_id),))
        return cursor.fetchone()[0]

    finally:
        conn.close()


def get_company_outputs(company_id, limit=100):
    """Get recent outputs for a company."""
    conn = get_connection()
    try:
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

    finally:
        conn.close()


def get_company_alerts(company_id, limit=50):
    """Get recent alerts for a company."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, alert_type, message, timestamp
            FROM alerts
            WHERE company_id = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (int(company_id), int(limit)))

        return cursor.fetchall()

    finally:
        conn.close()


def get_recent_scores(company_id, limit=50):
    """Get recent quality scores oldest first."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT quality_score
            FROM outputs
            WHERE company_id = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (int(company_id), int(limit)))

        rows   = cursor.fetchall()
        scores = [float(row[0]) for row in rows]
        scores.reverse()
        return scores

    finally:
        conn.close()


def get_company_stats(company_id):
    """Get summary statistics for a company."""
    conn = get_connection()
    try:
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

    finally:
        conn.close()


def get_all_companies():
    """Get all registered companies."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, company_name, created_at, plan
            FROM companies
            ORDER BY created_at DESC
        """)
        return cursor.fetchall()

    finally:
        conn.close()