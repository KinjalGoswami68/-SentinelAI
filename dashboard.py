# dashboard.py

import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")


st.set_page_config(
    page_title = "SentinelAI Dashboard",
    page_icon  = "🛡️",
    layout     = "wide"
)

DB_FILE         = "sentinelai.db"
WARMUP_REQUIRED = 50


def db_connect():
    """Open database connection."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_all_companies():
    """Get all registered companies for sidebar."""
    try:
        conn   = db_connect()
        cursor = conn.execute(
            "SELECT id, company_name, created_at, plan "
            "FROM companies ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_outputs_df(company_id):
    """Get all outputs for a company as DataFrame."""
    try:
        conn = db_connect()
        df   = pd.read_sql_query(
            "SELECT * FROM outputs "
            "WHERE company_id = ? "
            "ORDER BY timestamp ASC",
            conn,
            params=(int(company_id),)
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


def get_alerts_df(company_id):
    """Get all alerts for a company as DataFrame."""
    try:
        conn = db_connect()
        df   = pd.read_sql_query(
            "SELECT * FROM alerts "
            "WHERE company_id = ? "
            "ORDER BY timestamp DESC",
            conn,
            params=(int(company_id),)
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


def get_stats(company_id):
    """Get summary statistics for a company."""
    try:
        conn = db_connect()

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
            "SELECT AVG(quality_score) FROM outputs "
            "WHERE company_id = ?",
            (int(company_id),)
        ).fetchone()[0]

        avg = round(float(avg_raw), 2) if avg_raw is not None else 0.0

        alerts = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE company_id = ?",
            (int(company_id),)
        ).fetchone()[0]

        good = conn.execute(
            "SELECT COUNT(*) FROM outputs "
            "WHERE company_id = ? "
            "AND is_anomaly = 0 "
            "AND quality_score > 6.0",
            (int(company_id),)
        ).fetchone()[0]

        conn.close()

        return {
            "total"    : total,
            "anomalies": anomalies,
            "avg"      : avg,
            "alerts"   : alerts,
            "good"     : good
        }

    except Exception:
        return {
            "total"    : 0,
            "anomalies": 0,
            "avg"      : 0.0,
            "alerts"   : 0,
            "good"     : 0
        }



# HEADER
st.title("🛡️ SentinelAI")
st.caption("LLM Quality Monitoring — Production Dashboard")


with st.sidebar:
    st.header("Companies")

    companies = get_all_companies()

    if not companies:
        st.warning("No companies registered yet.")
        st.info(
            "Register a company first:\n\n"
            "POST /register\n"
            "{\"company_name\": \"YourName\"}"
        )
        selected_id   = None
        selected_name = None

    else:
        options = {
            f"{row[1]}  (ID {row[0]})": row[0]
            for row in companies
        }

        label         = st.selectbox(
            "Select Company",
            list(options.keys())
        )
        selected_id   = options[label]
        selected_name = label.split("  (ID")[0]

    st.divider()

    # Manual refresh button
    if st.button(" Refresh Dashboard"):
        st.rerun()

    st.caption(
        f"Last updated: {datetime.now().strftime('%H:%M:%S')}"
    )



if selected_id is None:

    
    st.divider()
    st.subheader(" Welcome to SentinelAI")
    st.write(
        "No companies registered yet. "
        "Follow the steps below to get started."
    )

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Step 1 — Register")
        st.code(
            'POST http://localhost:8000/register\n\n'
            '{\n'
            '  "company_name": "YourStartup"\n'
            '}',
            language="json"
        )
        st.caption(
            "You receive an API key in the response. "
            "Save it — you cannot retrieve it again."
        )

    with col2:
        st.markdown("### Step 2 — Send AI Outputs")
        st.code(
            'POST http://localhost:8000/ingest\n'
            'Header: X-API-Key: your_key\n\n'
            '{\n'
            '  "prompt": "user question here",\n'
            '  "response": "ai answer here",\n'
            '  "quality_score": 8.5\n'
            '}',
            language="json"
        )
        st.caption(
            f"Send {WARMUP_REQUIRED}+ outputs to "
            "activate ML detection automatically."
        )

    st.divider()
    st.markdown("### Step 3 — View Dashboard")
    st.write(
        "After sending outputs — come back here. "
        "Your company will appear in the sidebar. "
        "Select it to see your monitoring data."
    )

else:

    
    # Company dashboard
    stats = get_stats(selected_id)
    df    = get_outputs_df(selected_id)

    # Warmup status banner
    good_count = stats["good"]

    if good_count < WARMUP_REQUIRED:
        remaining = WARMUP_REQUIRED - good_count
        pct       = good_count / WARMUP_REQUIRED

        st.warning(
            f" **Warmup Mode** — "
            f"Send {remaining} more good quality outputs "
            f"(quality_score above 6.0) to activate ML detection."
        )
        st.progress(
            pct,
            text=f"Progress: {good_count}/{WARMUP_REQUIRED} outputs"
        )
        st.info(
            "During warmup — all outputs are stored but not analysed. "
            "Detection activates automatically once warmup completes."
        )

    else:
        st.success("ML Detection Active — monitoring all outputs")

    st.divider()

    
    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        label = "Total Outputs",
        value = stats["total"]
    )

    anomaly_rate = (
        round(stats["anomalies"] / stats["total"] * 100, 1)
        if stats["total"] > 0 else 0.0
    )
    c2.metric(
        label      = " Anomalies Detected",
        value      = stats["anomalies"],
        delta      = f"{anomaly_rate}% of total",
        delta_color= "inverse"
    )

    c3.metric(
        label = "Average Quality Score",
        value = f"{stats['avg']} / 10"
    )

    c4.metric(
        label = " Alerts Fired",
        value = stats["alerts"]
    )

    st.divider()

    
    # Quality score chart
    if len(df) == 0:

        st.info(
            "No outputs received yet. "
            "Start sending data to the /ingest endpoint."
        )

        st.code(
            'import requests\n\n'
            'requests.post(\n'
            '    "http://localhost:8000/ingest",\n'
            '    headers={"X-API-Key": "your_api_key"},\n'
            '    json={\n'
            '        "prompt": "What is your return policy?",\n'
            '        "response": "You can return within 30 days.",\n'
            '        "quality_score": 9.0\n'
            '    }\n'
            ')',
            language="python"
        )

    else:

        # Quality score over time chart
        st.subheader("Quality Score Over Time")
        st.caption(
            "Each point is one AI output. "
            "Drops below normal indicate quality problems."
        )

        chart_df = df[["timestamp", "quality_score"]].copy()
        chart_df = chart_df.set_index("timestamp")
        st.line_chart(chart_df, use_container_width=True)

        st.divider()

        
        # Anomalies section
        st.subheader(" Detected Anomalies")

        anomalies_df = df[df["is_anomaly"] == 1].copy()

        if len(anomalies_df) == 0:
            st.success(
                "No anomalies detected. "
                "Your AI is performing normally."
            )
        else:
            st.error(
                f"Found {len(anomalies_df)} anomalous output(s). "
                f"Check details below."
            )

            for _, row in anomalies_df.iterrows():
                sim_text = (
                    f"Similarity: {round(float(row['similarity']), 3)}"
                    if row.get("similarity") is not None
                    else "Similarity: not calculated yet"
                )

                with st.expander(
                    f" Score: {row['quality_score']} / 10  "
                    f"| {row['timestamp']}"
                ):
                    st.write(f"**Prompt:**")
                    st.write(row["prompt"])
                    st.write(f"**AI Response:**")
                    st.write(row["response"])
                    st.write(f"**Quality Score:** {row['quality_score']} / 10")
                    st.write(f"**{sim_text}**")
                    st.write(f"**Detected at:** {row['timestamp']}")

        st.divider()

        
        # All outputs table
        st.subheader("All Monitored Outputs")

        display = df.copy()
        display["Status"] = display["is_anomaly"].apply(
            lambda x: "Anomaly" if int(x) == 1 else "Normal"
        )

        # Round similarity for display
        if "similarity" in display.columns:
            display["similarity"] = display["similarity"].apply(
                lambda x: round(float(x), 3) if x is not None else None
            )

        st.dataframe(
            display[[
                "id",
                "prompt",
                "response",
                "quality_score",
                "similarity",
                "Status",
                "timestamp"
            ]].rename(columns={
                "id"           : "ID",
                "prompt"       : "Prompt",
                "response"     : "AI Response",
                "quality_score": "Score",
                "similarity"   : "Similarity",
                "timestamp"    : "Time"
            }),
            use_container_width = True,
            height              = 400
        )

        st.divider()

        # Alert history
        st.subheader("Alert History")

        alerts_df = get_alerts_df(selected_id)

        if len(alerts_df) == 0:
            st.info("No alerts fired yet.")
        else:
            st.dataframe(
                alerts_df[[
                    "id",
                    "alert_type",
                    "message",
                    "timestamp"
                ]].rename(columns={
                    "id"        : "ID",
                    "alert_type": "Type",
                    "message"   : "Message",
                    "timestamp" : "Time"
                }),
                use_container_width = True
            )

st.divider()
st.caption("🛡️ SentinelAI — Built by Kinjal Goswami")
st.caption("Watching your AI 24/7 so you do not have to")