# main.py

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import numpy as np
import warnings
warnings.filterwarnings("ignore")


from embeddings       import embed, build_baseline, compute_similarity, find_threshold
from anomaly_detector import SentinelDetector
from drift_detector   import CUSUMDetector
from database import (
    create_tables,
    register_company,
    get_company_by_api_key,
    save_output,
    save_alert,
    get_baseline_outputs,
    get_output_count,
    get_good_output_count,
    get_company_outputs,
    get_company_alerts,
    get_recent_scores,
    get_company_stats,
    get_all_companies
)
from alerts import send_slack_alert

app = FastAPI(
    title       = "SentinelAI",
    description = (
        "LLM Quality Monitoring. "
        "Detects when your AI starts giving wrong answers "
        "before your users notice."
    ),
    version = "2.0.0",
    debug   = True
)


# Minimum good outputs before detection activates
# Company sends outputs — first WARMUP_REQUIRED
WARMUP_REQUIRED = 50

# Refresh ML models every N requests per company
# This keeps models updated with new data
# without rebuilding on every single request
REFRESH_EVERY = 100


# Structure per company_id:
# {
#   "warmup"        : bool,
#   "baseline"      : numpy array or None,
#   "threshold"     : float,
#   "detector"      : SentinelDetector or None,
#   "cusum"         : CUSUMDetector or None,
#   "good_count"    : int,
#   "request_count" : int
# }
_cache = {}

create_tables()



def _build_state(company_id):
    good_responses = get_baseline_outputs(company_id, limit=500)
    good_count     = len(good_responses)
    total_count    = get_output_count(company_id)

    
    if good_count < WARMUP_REQUIRED:
        return {
            "warmup"       : True,
            "baseline"     : None,
            "threshold"    : 0.50,
            "detector"     : None,
            "cusum"        : CUSUMDetector(),
            "good_count"   : good_count,
            "total_count"  : total_count,
            "request_count": 0
        }

    
    good_vectors = embed(good_responses)

    # Build baseline vector
    baseline = np.mean(good_vectors, axis=0)

    # Find optimal threshold for this company's data
    threshold = find_threshold(good_responses, vectors=good_vectors)

    
    detector = SentinelDetector()
    detector.train(good_vectors)

    return {
        "warmup"       : False,
        "baseline"     : baseline,
        "threshold"    : threshold,
        "detector"     : detector,
        "cusum"        : CUSUMDetector(),
        "good_count"   : good_count,
        "total_count"  : total_count,
        "request_count": 0
    }


def _get_state(company_id):
    if company_id not in _cache:
        _cache[company_id] = _build_state(company_id)
        return _cache[company_id]

    state = _cache[company_id]

    
    state["request_count"] += 1

    if state["request_count"] % REFRESH_EVERY == 0:
        
        existing_cusum = state.get("cusum")

        new_state = _build_state(company_id)

        # Restore CUSUM if it existed
        if existing_cusum is not None and not new_state["warmup"]:
            new_state["cusum"] = existing_cusum

        _cache[company_id] = new_state

    return _cache[company_id]


class RegisterData(BaseModel):
    company_name: str


class OutputData(BaseModel):
    prompt        : str
    response      : str
    quality_score : float


@app.get("/")
def root():
    return {
        "system" : "SentinelAI",
        "version": "2.0.0",
        "status" : "running",
        "docs"   : "/docs",
        "quickstart": [
            "Step 1: POST /register  with {company_name}",
            "Step 2: Copy api_key from response",
            f"Step 3: POST /ingest with X-API-Key header — send {WARMUP_REQUIRED}+ outputs",
            "Step 4: GET  /dashboard with X-API-Key to see monitoring data"
        ]
    }

# Any company registers here.
# Receives unique API key.
# Uses that key for all future requests.
@app.post("/register")
def register(data: RegisterData):
    name = data.company_name.strip() if data.company_name else ""

    if not name:
        raise HTTPException(
            status_code = 400,
            detail      = "company_name cannot be empty"
        )

    if len(name) < 2:
        raise HTTPException(
            status_code = 400,
            detail      = "company_name must be at least 2 characters"
        )

    if len(name) > 100:
        raise HTTPException(
            status_code = 400,
            detail      = "company_name must be under 100 characters"
        )

    result = register_company(name)

    return {
        "success"     : True,
        "company_name": name,
        "company_id"  : result["company_id"],
        "api_key"     : result["api_key"],
        "next_steps"  : {
            "1_save_key" : "Save your api_key — it cannot be retrieved again",
            "2_send_data": f"POST to /ingest with header X-API-Key: {result['api_key']}",
            "3_warmup"   : f"Send {WARMUP_REQUIRED}+ outputs to activate detection",
            "4_dashboard": "GET /dashboard with your API key to see monitoring data"
        }
    }

@app.get("/health")
def health():
    companies = get_all_companies()
    return {
        "status"           : "healthy",
        "version"          : "2.0.0",
        "companies_active" : len(companies),
        "timestamp"        : datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }



# Fires Slack alert if problem detected.

@app.post("/ingest")
def ingest(
    data      : OutputData,
    x_api_key : Optional[str] = Header(None)
):
   
    if not x_api_key:
        raise HTTPException(
            status_code = 401,
            detail      = "Missing X-API-Key header"
        )

    company = get_company_by_api_key(x_api_key)

    if not company:
        raise HTTPException(
            status_code = 401,
            detail      = "Invalid API key"
        )

    company_id = company["id"]

    
    prompt   = data.prompt.strip()   if data.prompt   else ""
    response = data.response.strip() if data.response else ""

    if not prompt:
        raise HTTPException(
            status_code = 400,
            detail      = "prompt cannot be empty"
        )

    if not response:
        raise HTTPException(
            status_code = 400,
            detail      = "response cannot be empty"
        )

    if not 0.0 <= data.quality_score <= 10.0:
        raise HTTPException(
            status_code = 400,
            detail      = "quality_score must be between 0.0 and 10.0"
        )

    
    if company_id in _cache and _cache[company_id].get("warmup"):
        fresh_count = get_good_output_count(company_id)
        if fresh_count >= WARMUP_REQUIRED:
            _cache.pop(company_id)

    
    state = _get_state(company_id)


    if state["warmup"]:
        save_output(
            company_id    = company_id,
            prompt        = prompt,
            response      = response,
            quality_score = data.quality_score,
            is_anomaly    = 0,
            similarity    = None
        )

        good_count = get_good_output_count(company_id)
        remaining  = max(0, WARMUP_REQUIRED - good_count)

        return {
            "status"          : "warmup",
            "detection_active": False,
            "message"         : f"Send {remaining} more good outputs to activate detection.",
            "warmup_progress" : f"{good_count}/{WARMUP_REQUIRED}",
            "outputs_stored"  : True
        }


    # Step 1: Embed the response
    response_vector = embed(response)[0]

    # Step 2: Cosine similarity check
    similarity = compute_similarity(
        response_vector,
        state["baseline"]
    )

    # Step 3: Isolation Forest check
    detection      = state["detector"].predict(response_vector)
    anomaly_by_if  = detection["is_anomaly"]

    # Step 4: Similarity threshold check
    anomaly_by_sim = similarity < state["threshold"]

    # Step 5: Combined decision
    is_anomaly = anomaly_by_if or anomaly_by_sim

    # Step 6: CUSUM drift check
    cusum_result = state["cusum"].update(data.quality_score)
    drift_alert  = cusum_result["alert"]

    # Step 7: Save to database
    save_output(
        company_id    = company_id,
        prompt        = prompt,
        response      = response,
        quality_score = data.quality_score,
        is_anomaly    = int(is_anomaly),
        similarity    = similarity
    )

    # Step 8: Send alerts if needed
    alert_sent = False

    if is_anomaly:
        reasons = []
        if anomaly_by_if:
            reasons.append(
                f"Isolation Forest flagged as outlier"
            )
        if anomaly_by_sim:
            reasons.append(
                f"Similarity {similarity} below threshold {state['threshold']}"
            )

        alert_msg = " | ".join(reasons)

        save_alert(
            company_id = company_id,
            alert_type = "anomaly",
            message    = f"Score: {data.quality_score} | {alert_msg}"
        )

        send_slack_alert(
            alert_type = "anomaly",
            message    = f"Company: {company['company_name']} | {alert_msg}",
            score      = data.quality_score,
            response   = response
        )

        alert_sent = True

    if drift_alert:
        save_alert(
            company_id = company_id,
            alert_type = "drift",
            message    = cusum_result["message"]
        )

        send_slack_alert(
            alert_type = "drift",
            message    = f"Company: {company['company_name']} | {cusum_result['message']}",
            score      = data.quality_score,
            response   = response
        )

        alert_sent = True

    # Step 9: Return result
    return {
        "status"          : "monitored",
        "detection_active": True,
        "is_anomaly"      : is_anomaly,
        "drift_alert"     : drift_alert,
        "similarity"      : similarity,
        "threshold"       : state["threshold"],
        "alert_sent"      : alert_sent,
        "company"         : company["company_name"],
        "timestamp"       : datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }



@app.get("/dashboard")
def dashboard_data(x_api_key: Optional[str] = Header(None)):
    if not x_api_key:
        raise HTTPException(
            status_code = 401,
            detail      = "Missing X-API-Key header"
        )

    company = get_company_by_api_key(x_api_key)

    if not company:
        raise HTTPException(
            status_code = 401,
            detail      = "Invalid API key"
        )

    company_id = company["id"]
    state      = _get_state(company_id)
    stats      = get_company_stats(company_id)
    outputs    = get_company_outputs(company_id, limit=100)
    alerts     = get_company_alerts(company_id, limit=50)
    scores     = get_recent_scores(company_id, limit=50)

    output_list = []
    for row in outputs:
        id_, prompt, response, score, anomaly, sim, timestamp = row
        output_list.append({
            "id"           : id_,
            "prompt"       : prompt,
            "response"     : response,
            "quality_score": score,
            "is_anomaly"   : bool(anomaly),
            "similarity"   : sim,
            "timestamp"    : timestamp
        })

    alert_list = []
    for row in alerts:
        id_, alert_type, message, timestamp = row
        alert_list.append({
            "id"        : id_,
            "alert_type": alert_type,
            "message"   : message,
            "timestamp" : timestamp
        })

    return {
        "company"          : company["company_name"],
        "detection_active" : not state["warmup"],
        "warmup_progress"  : f"{state['good_count']}/{WARMUP_REQUIRED}",
        "threshold"        : state["threshold"],
        "stats"            : stats,
        "recent_outputs"   : output_list,
        "recent_alerts"    : alert_list,
        "score_history"    : scores
    }