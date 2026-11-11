# alerts.py

import requests
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import warnings
warnings.filterwarnings("ignore")

load_dotenv()

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


def send_slack_alert(alert_type, message, score, response):
    """ Input:
        alert_type - "anomaly" or "drift"
        message    - description of problem
        score      - quality score that triggered alert
        response   - the problematic AI output text
    
    Output:
        True if sent successfully, False otherwise
    """
    if not SLACK_WEBHOOK_URL:
        print("Warning: SLACK_WEBHOOK_URL not set in .env file")
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if alert_type == "anomaly":
        title = "ANOMALY DETECTED"
    elif alert_type == "drift":
        title = "QUALITY DRIFT DETECTED"
    else:
        title = "ALERT"

    slack_message = {
        "text": f" ShiftLens — {title}",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f" ShiftLens — {title}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Alert Type:*\n{alert_type}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": (f"*Quality Score:*\n{score}/10" if score is not None
                                 else "*Quality Score:*\nnot sent")
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Time:*\n{timestamp}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Details:*\n{message}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Flagged Output:*\n```{str(response)[:300]}```"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_Sent by ShiftLens — LLM Quality Monitor_"
                }
            }
        ]
    }

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            data    = json.dumps(slack_message),
            headers = {"Content-Type": "application/json"},
            timeout = 10
        )

        if resp.status_code == 200:
            print(f"Slack alert sent: {alert_type}")
            return True
        else:
            print(f"Slack error {resp.status_code}: {resp.text}")
            return False

    # SSLError is a subclass of ConnectionError, so it must be caught first.
    # It means TLS certificate verification failed (e.g. antivirus HTTPS
    # scanning re-signing traffic), not that the internet is down.
    except requests.exceptions.SSLError:
        print("Slack alert failed: TLS certificate verification failed (SSLError)")
        return False

    except requests.exceptions.ConnectionError:
        print("Slack alert failed: could not connect to Slack (ConnectionError)")
        return False

    except requests.exceptions.Timeout:
        print("Slack alert failed: Request timed out")
        return False

    except Exception as e:
        # Class name only: requests exception messages can contain the full
        # request URL, and for Slack the URL itself is the secret.
        print(f"Slack alert failed: {type(e).__name__}")
        return False

def send_slack_text(text):
    """Post one mrkdwn message (used for investigation notes). Incoming webhooks
    cannot reply in a thread (they return no message id), so the note is a
    separate message after the alert. True if Slack accepted it."""
    if not SLACK_WEBHOOK_URL:
        print("Warning: SLACK_WEBHOOK_URL not set")
        return False
    body = {"text": text[:3000],
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}]}
    try:
        resp = requests.post(SLACK_WEBHOOK_URL, data=json.dumps(body),
                             headers={"Content-Type": "application/json"}, timeout=10)
    except requests.exceptions.SSLError:
        print("Slack note failed: TLS certificate verification failed (SSLError)")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Slack note failed: {type(e).__name__}")   # never the URL: it is the secret
        return False
    if resp.status_code != 200:
        print(f"Slack note error {resp.status_code}: {resp.text[:100]}")
        return False
    print("Slack message sent")
    return True
