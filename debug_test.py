import requests

r = requests.post(
    "http://localhost:8000/ingest",
    headers={"X-API-Key": "sk-dac3ce95ba65df228e2f7dc72722bee0"},
    json={
        "prompt": "What is your return policy?",
        "response": "You can return within 30 days.",
        "quality_score": 9.0
    }
)
print(r.status_code)
print(r.text)