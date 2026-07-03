# SentinelAI

from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")


from database import (
    create_tables,
    save_output,
    save_alert,
    get_all_outputs,
    get_all_alerts,
    get_recent_scores,
    get_stats
)
from alerts import send_slack_alert


from sentence_transformers import SentenceTransformer
from sklearn.ensemble import IsolationForest
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np


app = FastAPI(
    title       = "SentinelAI",
    description = "LLM Quality Monitoring System",
    version     = "1.0.0"
)


print("Loading ML model...")
model = SentenceTransformer('all-MiniLM-L6-v2')
print("Model loaded!")


baseline_outputs = [
    "Your refund will be processed within 3 to 5 business days.",
    "You can return the product within 30 days of purchase.",
    "Your order has been shipped and will arrive in 2 days.",
    "Our customer support is available from 9am to 6pm.",
    "You can track your order using the link sent to your email.",
    "We accept Visa, Mastercard, and UPI payments.",
    "Free shipping is available on orders above 500 rupees.",
    "Your password can be reset from the login page.",
    "Your account has been created successfully.",
    "The item is currently in stock and ready to ship.",
]

print("Creating baseline...")
baseline_vectors = model.encode(baseline_outputs)
baseline         = np.mean(baseline_vectors, axis=0)

print("Training Isolation Forest...")
detector = IsolationForest(
    contamination = 0.05,
    n_estimators  = 200,
    random_state  = 42
)
detector.fit(baseline_vectors)
print("System ready!")

cusum_state = {
    "cumulative_sum" : 0.0,
    "target"         : 8.0,
    "threshold"      : 3.0,
    "drift"          : 0.5
}

create_tables()

class OutputData(BaseModel):
    prompt        : str
    response      : str
    quality_score : float

    @app.get("/")
    def root():
     return {
        "system"  : "SentinelAI",
        "status"  : "running",
        "version" : "1.0.0",
        "message" : "LLM Quality Monitor is active"
    }

@app.get("/health")
def health():
    stats = get_stats()
    return {
        "status"          : "healthy",
        "total_outputs"   : stats["total_outputs"],
        "total_anomalies" : stats["total_anomalies"],
        "avg_score"       : stats["avg_score"],
        "total_alerts"    : stats["total_alerts"],
        "timestamp"       : datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

@app.post("/ingest")
def ingest(data: OutputData):

    
    response_vector = model.encode([data.response])[0]

    # Cosine similarity check
    similarity = cosine_similarity(
        [response_vector],
        [baseline]
    )[0][0]
    similarity = round(float(similarity), 4)

    
    prediction = detector.predict([response_vector])[0]
    is_anomaly = 1 if prediction == -1 else 0

    
    drop            = cusum_state["target"] - data.quality_score
    meaningful_drop = drop - cusum_state["drift"]
    cusum_state["cumulative_sum"] = max(
        0.0,
        cusum_state["cumulative_sum"] + meaningful_drop
    )
    drift_alert = cusum_state["cumulative_sum"] >= cusum_state["threshold"]

    if drift_alert:
        cusum_state["cumulative_sum"] = 0.0

    # Save to database
    save_output(
        prompt        = data.prompt,
        response      = data.response,
        quality_score = data.quality_score,
        is_anomaly    = is_anomaly
    )

    # Send alerts if needed
    alert_sent = False

    if is_anomaly == 1:
        save_alert(
            alert_type = "anomaly",
            message    = f"Anomalous output - score {data.quality_score}"
        )
        send_slack_alert(
            alert_type = "anomaly",
            message    = "Anomalous output detected",
            score      = data.quality_score,
            response   = data.response
        )
        alert_sent = True

    if drift_alert:
        save_alert(
            alert_type = "drift",
            message    = "Quality drift detected by CUSUM"
        )
        send_slack_alert(
            alert_type = "drift",
            message    = "Quality drift detected by CUSUM",
            score      = data.quality_score,
            response   = data.response
        )
        alert_sent = True

    # Return result
    return {
        "received"     : True,
        "similarity"   : similarity,
        "is_anomaly"   : bool(is_anomaly),
        "drift_alert"  : drift_alert,
        "alert_sent"   : alert_sent,
        "quality_score": data.quality_score,
        "timestamp"    : datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


@app.get("/outputs")
def get_outputs():
    rows    = get_all_outputs()
    outputs = []
    for row in rows:
        id, prompt, response, score, anomaly, timestamp = row
        outputs.append({
            "id"           : id,
            "prompt"       : prompt,
            "response"     : response,
            "quality_score": score,
            "is_anomaly"   : bool(anomaly),
            "timestamp"    : timestamp
        })
    return {"outputs": outputs, "total": len(outputs)}


@app.get("/alerts")
def get_alerts():
    rows   = get_all_alerts()
    alerts = []
    for row in rows:
        id, alert_type, message, timestamp = row
        alerts.append({
            "id"        : id,
            "alert_type": alert_type,
            "message"   : message,
            "timestamp" : timestamp
        })
    return {"alerts": alerts, "total": len(alerts)}

@app.get("/stats")
def get_statistics():
    return get_stats()