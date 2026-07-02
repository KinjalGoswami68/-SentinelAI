
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

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if alert_type == "anomaly":
        emoji = "🚨"
        title = "ANOMALY DETECTED"
    elif alert_type == "drift":
        emoji = "📉"
        title = "QUALITY DRIFT DETECTED"
    else:
        emoji = "⚠️"
        title = "ALERT"

    slack_message = {
        "text": f"{emoji} SentinelAI Alert - {title}",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} SentinelAI - {title}"
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
                        "text": f"*Problem:*\n{message}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Flagged Output:*\n```{response[:200]}```"
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
        response_from_slack = requests.post(
            SLACK_WEBHOOK_URL,
            data    = json.dumps(slack_message),
            headers = {"Content-Type": "application/json"},
            timeout = 10
        )

        if response_from_slack.status_code == 200:
            print("Alert sent to Slack successfully")
            return True
        else:
            print(f"Slack error: {response_from_slack.status_code}")
            return False

    except Exception as e:
        print(f"Error sending alert: {e}")
        return False


def send_test_alert():
    print("Sending test alert to Slack...")
    success = send_slack_alert(
        alert_type = "anomaly",
        message    = "This is a test alert from SentinelAI",
        score      = 2.0,
        response   = "This is a fake bad AI output for testing."
    )
    if success:
        print("Success - check your Slack channel")
    else:
        print("Failed - check your webhook URL")
    return success


if __name__ == "__main__":

    print("=" * 50)
    print("SENTINELAI - ALERTS TEST")
    print("=" * 50)
    print()

    if not SLACK_WEBHOOK_URL:
        print("ERROR: SLACK_WEBHOOK_URL not found in .env file")
        print("Add this line to your .env file:")
        print("SLACK_WEBHOOK_URL=your_webhook_url_here")
    else:
        send_test_alert()

    print()
    print("=" * 50)
    print("alerts.py complete")
    print("Next file: main.py")
    print("=" * 50)