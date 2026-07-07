# database.py

import sqlite3
import secrets
from datetime import datetime

DB_FILE = "sentinelai.db"


def get_connection():
    """
    Open and return a database connection.
    Enables foreign key support.
    """
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_tables():
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT    NOT NULL,
                api_key      TEXT    NOT NULL UNIQUE,
                created_at   TEXT    NOT NULL,
                plan         TEXT    NOT NULL DEFAULT 'free'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS outputs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
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
        # Always close — even if error occurs
        conn.close()


def register_company(company_name): 

    """ Input:  company_name — string
    Output: dict with company_id and api_key
    """
    conn      = get_connection()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    api_key   = "sk-" + secrets.token_hex(16)

    try:
        conn.execute("""
            INSERT INTO companies
            (company_name, api_key, created_at, plan)
            VALUES (?, ?, ?, ?)
        """, (company_name.strip(), api_key, timestamp, "free"))

        conn.commit()

        cursor = conn.execute("SELECT last_insert_rowid()")
        row_id = cursor.fetchone()[0]

        return {
            "company_id": row_id,
            "api_key"   : api_key
        }

    finally:
        conn.close()


def get_company_by_api_key(api_key):
    if not api_key or not api_key.strip():
        return None

    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT id, company_name, plan
            FROM companies
            WHERE api_key = ?
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
    Input:
        company_id    — int
        prompt        — string (user question)
        response      — string (AI answer)
        quality_score — float 0.0 to 10.0
        is_anomaly    — int 0 or 1
        similarity    — float (optional, cosine similarity score)

    Output:
        row id of the inserted record
    """
    conn      = get_connection()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn.execute("""
            INSERT INTO outputs
            (company_id, prompt, response, quality_score,
             is_anomaly, similarity, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            int(company_id),
            str(prompt),
            str(response),
            float(quality_score),
            int(is_anomaly),
            float(similarity) if similarity is not None else None,
            timestamp
        ))

        conn.commit()

        cursor = conn.execute("SELECT last_insert_rowid()")
        row_id = cursor.fetchone()[0]
        return row_id

    finally:
        conn.close()


def save_alert(company_id, alert_type, message):
    conn      = get_connection()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn.execute("""
            INSERT INTO alerts
            (company_id, alert_type, message, timestamp)
            VALUES (?, ?, ?, ?)
        """, (int(company_id), str(alert_type), str(message), timestamp))

        conn.commit()

    finally:
        conn.close()


def get_baseline_outputs(company_id, limit=500):

    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT response
            FROM outputs
            WHERE company_id    = ?
            AND   is_anomaly    = 0
            AND   quality_score > 6.0
            ORDER BY timestamp ASC
            LIMIT ?
        """, (int(company_id), int(limit)))

        rows = cursor.fetchall()
        return [row[0] for row in rows]

    finally:
        conn.close()


def get_output_count(company_id):
    
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM outputs WHERE company_id = ?",
            (int(company_id),)
        )
        return cursor.fetchone()[0]

    finally:
        conn.close()


def get_good_output_count(company_id):
    
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT COUNT(*)
            FROM outputs
            WHERE company_id    = ?
            AND   is_anomaly    = 0
            AND   quality_score > 6.0
        """, (int(company_id),))
        return cursor.fetchone()[0]

    finally:
        conn.close()


def get_company_outputs(company_id, limit=100):
    
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT id, prompt, response, quality_score,
                   is_anomaly, similarity, timestamp
            FROM outputs
            WHERE company_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (int(company_id), int(limit)))

        return cursor.fetchall()

    finally:
        conn.close()


def get_company_alerts(company_id, limit=50):
    
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT id, alert_type, message, timestamp
            FROM alerts
            WHERE company_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (int(company_id), int(limit)))

        return cursor.fetchall()

    finally:
        conn.close()


def get_recent_scores(company_id, limit=50):
    
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT quality_score
            FROM outputs
            WHERE company_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (int(company_id), int(limit)))

        rows   = cursor.fetchall()
        scores = [float(row[0]) for row in rows]

        
        scores.reverse()
        return scores

    finally:
        conn.close()


def get_company_stats(company_id):
    
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM outputs WHERE company_id = ?",
            (int(company_id),)
        ).fetchone()[0]

        anomalies = conn.execute(
            "SELECT COUNT(*) FROM outputs "
            "WHERE company_id = ? AND is_anomaly = 1",
            (int(company_id),)
        ).fetchone()[0]

        avg_raw = conn.execute(
            "SELECT AVG(quality_score) FROM outputs WHERE company_id = ?",
            (int(company_id),)
        ).fetchone()[0]

        avg_score = round(float(avg_raw), 2) if avg_raw is not None else 0.0

        alert_count = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE company_id = ?",
            (int(company_id),)
        ).fetchone()[0]

        good_count = conn.execute(
            "SELECT COUNT(*) FROM outputs "
            "WHERE company_id = ? "
            "AND is_anomaly = 0 "
            "AND quality_score > 6.0",
            (int(company_id),)
        ).fetchone()[0]

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
    
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT id, company_name, created_at, plan
            FROM companies
            ORDER BY created_at DESC
        """)
        return cursor.fetchall()

    finally:
        conn.close()