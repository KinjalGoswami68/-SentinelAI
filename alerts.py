# alerts.py

import requests
import json
import os
from datetime import datetime
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

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if alert_type == "anomaly":
        title = "ANOMALY DETECTED"
    elif alert_type == "drift":
        title = "QUALITY DRIFT DETECTED"
    else:
        title = "ALERT"

    slack_message = {
        "text": f" SentinelAI — {title}",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f" SentinelAI — {title}"
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
                        "text": f"*Quality Score:*\n{score}/10"
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
                    "text": "_Sent by SentinelAI — LLM Quality Monitor_"
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

    except requests.exceptions.ConnectionError:
        print("Slack alert failed: No internet connection")
        return False

    except requests.exceptions.Timeout:
        print("Slack alert failed: Request timed out")
        return False

    except Exception as e:
        print(f"Slack alert failed: {e}")
        return False