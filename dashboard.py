
# dashboard.py

import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")

st.set_page_config(
    page_title = "SentinelAI",
    page_icon  = "🛡️",
    layout     = "wide"
)

API_URL = "https://sentinelai-qqph.onrender.com"


def api_get(endpoint, api_key=None):
    """Make GET request to live API."""
    try:
        headers = {}
        if api_key:
            headers["X-API-Key"] = api_key

        r = requests.get(
            f"{API_URL}{endpoint}",
            headers = headers,
            timeout = 30
        )

        if r.status_code == 200:
            return r.json()
        return None

    except Exception:
        return None


def api_post(endpoint, data, api_key=None):
    """Make POST request to live API."""
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key

        r = requests.post(
            f"{API_URL}{endpoint}",
            json    = data,
            headers = headers,
            timeout = 30
        )

        if r.status_code == 200:
            return r.json()
        return None

    except Exception:
        return None


def register_company(company_name):
    """Register a new company and get API key."""
    result = api_post("/register", {"company_name": company_name})
    return result


def get_dashboard_data(api_key):
    """Get all monitoring data for a company."""
    return api_get("/dashboard", api_key=api_key)


def check_server():
    """Check if Render server is awake."""
    result = api_get("/health")
    return result is not None


st.title("🛡️ SentinelAI")
st.caption("LLM Quality Monitoring")


# SIDEBAR
with st.sidebar:
    st.header(" Your API Key")

    st.caption(
        "Enter your API key to see your monitoring data. "
        "Register below if you do not have one yet."
    )

    api_key_input = st.text_input(
        "API Key",
        type        = "password",
        placeholder = "sk-..."
    )

    st.divider()

    # Register new company
    st.header("Register New Company")
    company_name_input = st.text_input(
        "Company Name",
        placeholder = "YourStartup"
    )

    if st.button("Register & Get API Key"):
        if not company_name_input.strip():
            st.error("Enter a company name first")
        else:
            with st.spinner("Registering..."):
                result = register_company(company_name_input.strip())
            if result:
                st.success("Registered successfully!")
                st.code(result["api_key"])
                st.caption(
                    "Copy this API key. "
                    "Paste it above to see your dashboard. "
                    "You cannot retrieve it again."
                )
            else:
                st.error(
                    "Registration failed. "
                    "Server may be waking up. "
                    "Wait 60 seconds and try again."
                )

    st.divider()

    if st.button(" Refresh"):
        st.rerun()

    st.caption(
        f"Updated: {datetime.now().strftime('%H:%M:%S')}"
    )


# MAIN CONTENT

if not api_key_input:
    
    st.divider()
    st.subheader(" Welcome to SentinelAI")
    st.write(
        "Monitor your AI chatbot quality in real time. "
        "Detect hallucinations before your users notice."
    )

    st.divider()

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("### Step 1")
        st.markdown("**Register your company** in the sidebar. Get your API key.")

    with col2:
        st.markdown("### Step 2")
        st.markdown("**Send AI outputs** to our API with your key.")
        st.code(
            'POST /ingest\n'
            'X-API-Key: your_key\n\n'
            '{\n'
            '  "prompt": "question",\n'
            '  "response": "answer",\n'
            '  "quality_score": 8.5\n'
            '}',
            language="json"
        )

    with col3:
        st.markdown("### Step 3")
        st.markdown("**Paste your API key** above to see your live dashboard.")

    st.divider()

    st.subheader("🔌 API Integration")
    st.code(
        'import requests\n\n'
        '# Send every AI output for monitoring\n'
        'requests.post(\n'
        f'    "{API_URL}/ingest",\n'
        '    headers={"X-API-Key": "your_api_key"},\n'
        '    json={\n'
        '        "prompt": user_question,\n'
        '        "response": ai_answer,\n'
        '        "quality_score": 8.5\n'
        '    }\n'
        ')',
        language="python"
    )

else:
    # API key entered — show company dashboard
    with st.spinner("Loading your dashboard..."):
        data = get_dashboard_data(api_key_input.strip())

    if data is None:
        st.error(
            "Could not load dashboard. "
            "Check your API key is correct. "
            "Server may be waking up — wait 60 seconds and refresh."
        )
        st.info(
            "If you just registered — your API key is correct. "
            "The server may be sleeping. "
            "Open this URL first to wake it up: "
            f"{API_URL}"
        )
    else:
        
        st.subheader(f" {data['company']}")

       
        if not data.get("detection_active"):
            progress_text = data.get("warmup_progress", "0/50")
            current, total = progress_text.split("/")
            pct = int(current) / int(total)

            st.warning(
                f" **Warmup Mode** — "
                f"Send more good quality outputs to activate detection. "
                f"Progress: {progress_text}"
            )
            st.progress(min(pct, 1.0))
        else:
            st.success(" ML Detection Active")

        st.divider()

        
        stats = data.get("stats", {})

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            label = " Total Monitored",
            value = stats.get("total_outputs", 0)
        )

        total    = stats.get("total_outputs", 1)
        anomalies = stats.get("total_anomalies", 0)
        rate     = round(anomalies / max(total, 1) * 100, 1)

        c2.metric(
            label      = " Anomalies",
            value      = anomalies,
            delta      = f"{rate}% rate",
            delta_color= "inverse"
        )

        c3.metric(
            label = " Avg Quality",
            value = f"{stats.get('avg_score', 0)} / 10"
        )

        c4.metric(
            label = " Alerts Fired",
            value = stats.get("total_alerts", 0)
        )

        st.divider()

        
        scores = data.get("score_history", [])

        if scores:
            st.subheader(" Quality Score Over Time")
            chart_df = pd.DataFrame({
                "Quality Score": scores
            })
            st.line_chart(chart_df, use_container_width=True)
            st.divider()

        
        outputs = data.get("recent_outputs", [])

        if outputs:
            # Anomalies section
            st.subheader(" Detected Anomalies")

            anomalous = [o for o in outputs if o.get("is_anomaly")]

            if not anomalous:
                st.success(" No anomalies detected. AI is performing normally.")
            else:
                st.error(f"Found {len(anomalous)} anomalous output(s)")

                for row in anomalous:
                    sim = row.get("similarity")
                    sim_text = (
                        f"Similarity: {round(float(sim), 3)}"
                        if sim is not None
                        else "Similarity: N/A"
                    )
                    with st.expander(
                        f" Score: {row['quality_score']} / 10 | {row['timestamp']}"
                    ):
                        st.write(f"**Prompt:** {row['prompt']}")
                        st.write(f"**AI Response:** {row['response']}")
                        st.write(f"**Quality Score:** {row['quality_score']} / 10")
                        st.write(f"**{sim_text}**")
                        st.write(f"**Detected at:** {row['timestamp']}")

            st.divider()

            # All outputs table
            st.subheader(" All Monitored Outputs")

            df = pd.DataFrame(outputs)
            df["Status"] = df["is_anomaly"].apply(
                lambda x: " Anomaly" if x else " Normal"
            )

            if "similarity" in df.columns:
                df["similarity"] = df["similarity"].apply(
                    lambda x: round(float(x), 3) if x is not None else None
                )

            st.dataframe(
                df[[
                    "id", "prompt", "response",
                    "quality_score", "similarity",
                    "Status", "timestamp"
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

        st.subheader(" Alert History")

        alerts = data.get("recent_alerts", [])

        if not alerts:
            st.info("No alerts fired yet.")
        else:
            alerts_df = pd.DataFrame(alerts)
            st.dataframe(
                alerts_df[[
                    "id", "alert_type", "message", "timestamp"
                ]].rename(columns={
                    "id"        : "ID",
                    "alert_type": "Type",
                    "message"   : "Message",
                    "timestamp" : "Time"
                }),
                use_container_width = True
            )

        st.divider()

        # Integration code
        st.subheader(" Integrate in 3 Lines")
        st.code(
            'import requests\n\n'
            'requests.post(\n'
            f'    "{API_URL}/ingest",\n'
            f'    headers={{"X-API-Key": "{api_key_input[:8]}..."}},\n'
            '    json={\n'
            '        "prompt": user_question,\n'
            '        "response": ai_answer,\n'
            '        "quality_score": 8.5\n'
            '    }\n'
            ')',
            language="python"
        )

# FOOTER
st.divider()
st.caption("🛡️ SentinelAI — by Kinjal Goswami")
st.caption("Watching your AI 24/7 so you do not have to")