 
# SentinelAI 
import sqlite3
import os
from datetime import datetime


# DATABASE FILE NAME
DB_FILE = "sentinelai.db"

def get_connection():
    connection = sqlite3.connect(DB_FILE)
    return connection

def create_tables():

    connection = get_connection()

    
    connection.execute("""
        CREATE TABLE IF NOT EXISTS outputs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt        TEXT    NOT NULL,
            response      TEXT    NOT NULL,
            quality_score REAL    NOT NULL,
            is_anomaly    INTEGER NOT NULL DEFAULT 0,
            timestamp     TEXT    NOT NULL
        )
    """)

   
    connection.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type TEXT    NOT NULL,
            message    TEXT    NOT NULL,
            timestamp  TEXT    NOT NULL
        )
    """)

    connection.commit()
    connection.close()

    print("Tables created successfully")


def save_output(prompt, response, quality_score, is_anomaly):

    connection = get_connection()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    connection.execute("""
        INSERT INTO outputs
        (prompt, response, quality_score, is_anomaly, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (prompt, response, quality_score, is_anomaly, timestamp))

    connection.commit()

    cursor = connection.execute("SELECT last_insert_rowid()")
    row_id = cursor.fetchone()[0]

    connection.close()

    return row_id


def save_alert(alert_type, message):

    connection = get_connection()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    connection.execute("""
        INSERT INTO alerts
        (alert_type, message, timestamp)
        VALUES (?, ?, ?)
    """, (alert_type, message, timestamp))

    connection.commit()
    connection.close()

    print(f"Alert saved: {alert_type} - {message}")


def get_all_outputs():

    connection = get_connection()

    cursor = connection.execute("""
        SELECT id, prompt, response,
               quality_score, is_anomaly, timestamp
        FROM outputs
        ORDER BY timestamp DESC
    """)

    rows = cursor.fetchall()
    connection.close()

    return rows


def get_all_alerts():

    connection = get_connection()

    cursor = connection.execute("""
        SELECT id, alert_type, message, timestamp
        FROM alerts
        ORDER BY timestamp DESC
    """)

    rows = cursor.fetchall()
    connection.close()

    return rows


def get_recent_scores(limit=50):

    connection = get_connection()

    cursor = connection.execute("""
        SELECT quality_score
        FROM outputs
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))

    rows = cursor.fetchall()
    connection.close()

    
    scores = [row[0] for row in rows]
    scores.reverse()

    return scores



def get_stats():

    connection = get_connection()

    cursor = connection.execute(
        "SELECT COUNT(*) FROM outputs"
    )
    total_outputs = cursor.fetchone()[0]

    cursor = connection.execute(
        "SELECT COUNT(*) FROM outputs WHERE is_anomaly = 1"
    )
    total_anomalies = cursor.fetchone()[0]

    cursor = connection.execute(
        "SELECT AVG(quality_score) FROM outputs"
    )
    avg_raw = cursor.fetchone()[0]

    if avg_raw is None:
        avg_score = 0.0
    else:
        avg_score = round(avg_raw, 2)

    cursor = connection.execute(
        "SELECT COUNT(*) FROM alerts"
    )
    total_alerts = cursor.fetchone()[0]

    connection.close()

    return {
        "total_outputs"  : total_outputs,
        "total_anomalies": total_anomalies,
        "avg_score"      : avg_score,
        "total_alerts"   : total_alerts,
    }



def clear_database():

    connection = get_connection()
    connection.execute("DELETE FROM outputs")
    connection.execute("DELETE FROM alerts")
    connection.commit()
    connection.close()

    print("Database cleared")



if __name__ == "__main__":

    print("=" * 50)
    print("SENTINELAI - DATABASE SETUP AND TEST")
    print("=" * 50)
    print()

    
    print("Step 1: Creating tables...")
    create_tables()
    print()

    
    print("Step 2: Clearing old test data...")
    clear_database()
    print()

    # Insert fake outputs
    print("Step 3: Inserting test outputs...")

    save_output(
        prompt        = "What is your return policy?",
        response      = "You can return within 30 days.",
        quality_score = 9.0,
        is_anomaly    = 0
    )

    save_output(
        prompt        = "When will my order arrive?",
        response      = "Your order arrives in 2 days.",
        quality_score = 8.5,
        is_anomaly    = 0
    )

    save_output(
        prompt        = "How do I get a refund?",
        response      = "Refunds take 6 months and need a lawyer.",
        quality_score = 2.0,
        is_anomaly    = 1
    )

    save_output(
        prompt        = "What payments do you accept?",
        response      = "We accept UPI and credit cards.",
        quality_score = 8.8,
        is_anomaly    = 0
    )

    save_output(
        prompt        = "Is my data safe?",
        response      = "We sell all your data to others.",
        quality_score = 1.5,
        is_anomaly    = 1
    )

    print("Test outputs inserted")
    print()

    
    print("Step 4: Inserting test alerts...")

    save_alert(
        alert_type = "anomaly",
        message    = "Anomalous output detected - score 2.0"
    )

    save_alert(
        alert_type = "drift",
        message    = "Quality drift detected - CUSUM fired"
    )
    print()

    
    print("Step 5: Reading stats...")
    print()

    stats = get_stats()
    print("STATS:")
    print(f"  Total outputs   : {stats['total_outputs']}")
    print(f"  Total anomalies : {stats['total_anomalies']}")
    print(f"  Average score   : {stats['avg_score']}")
    print(f"  Total alerts    : {stats['total_alerts']}")
    print()

    
    print("ALL OUTPUTS:")
    print("-" * 50)
    outputs = get_all_outputs()
    for row in outputs:
        id, prompt, response, score, anomaly, timestamp = row
        flag = "ANOMALY" if anomaly == 1 else "normal"
        print(f"ID       : {id}")
        print(f"Prompt   : {prompt}")
        print(f"Response : {response}")
        print(f"Score    : {score}")
        print(f"Status   : {flag}")
        print(f"Time     : {timestamp}")
        print("-" * 50)
    print()

    
    print("ALL ALERTS:")
    print("-" * 50)
    all_alerts = get_all_alerts()
    for row in all_alerts:
        id, alert_type, message, timestamp = row
        print(f"ID      : {id}")
        print(f"Type    : {alert_type}")
        print(f"Message : {message}")
        print(f"Time    : {timestamp}")
        print("-" * 50)
    print()

    
    print("RECENT SCORES FOR CUSUM:")
    scores = get_recent_scores()
    print(f"  {scores}")
    print()

    
    print("=" * 50)
    print("DATABASE FILE CHECK")
    print("=" * 50)
    if os.path.exists(DB_FILE):
        size = os.path.getsize(DB_FILE)
        print(f"File created : {DB_FILE}")
        print(f"File size    : {size} bytes")
    print()
    print("Functions available for other files:")
    print("  save_output()       - save new AI output")
    print("  save_alert()        - save new alert")
    print("  get_all_outputs()   - read all outputs")
    print("  get_all_alerts()    - read all alerts")
    print("  get_recent_scores() - scores for CUSUM")
    print("  get_stats()         - summary numbers")
    print()
   
    print("database.py complete")
    print("Next file: alerts.py")
    