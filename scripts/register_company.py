"""
Register a company on the live API and save its API key to a gitignored file.

  python scripts/register_company.py "Volt Electronics support bot" volt_api_key.txt

The key is written to the file only; the console shows a masked key. Copy the
file's contents into the GitHub secret by hand, then keep the file local.

Env: BASE_URL (default https://sentinelai-qqph.onrender.com)
"""

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import dev_truststore  # noqa: E402

dev_truststore.maybe_enable()

BASE_URL = os.environ.get("BASE_URL", "https://sentinelai-qqph.onrender.com").rstrip("/")


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    name, key_file = sys.argv[1], ROOT / sys.argv[2]
    if key_file.exists():
        print(f"ERROR: {key_file.name} already exists; not registering a second company.")
        return 1

    r = requests.post(f"{BASE_URL}/register", json={"company_name": name}, timeout=90)
    if r.status_code != 200:
        print(f"ERROR: /register HTTP {r.status_code} {r.text[:200]}")
        return 1
    data = r.json()
    key  = data["api_key"]
    key_file.write_text(key + "\n", encoding="utf-8")
    print(f"Registered '{name}' as company_id {data.get('company_id')} on {BASE_URL}")
    print(f"API key {key[:6]}...{key[-4:]} saved to {key_file.name} (gitignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
