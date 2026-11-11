# dev_truststore.py
#
# Local-dev only: make outbound HTTPS (Slack, Groq) trust the OS certificate
# store instead of certifi's bundle. Needed on a laptop whose antivirus
# (here: Avast Web Shield) re-signs HTTPS with its own root certificate.
#
# Off unless SHIFTLENS_DEV_TRUSTSTORE=1. Never set it on Render or Actions;
# truststore is in requirements-dev.txt only, so even if the variable were set
# there, this prints a warning and changes nothing.

import os

from dotenv import load_dotenv

ENV_VAR = "SHIFTLENS_DEV_TRUSTSTORE"
OLD_ENV_VAR = "SENTINEL_DEV_TRUSTSTORE"   # name before the rename (2026-11-11), still accepted


def maybe_enable():
    load_dotenv()   # so the flag can live in the local .env file
    if os.environ.get(ENV_VAR) != "1" and os.environ.get(OLD_ENV_VAR) != "1":
        return False

    try:
        import truststore
    except ImportError:
        print(f"WARNING: {ENV_VAR}=1 but truststore is not installed "
              "(pip install -r requirements-dev.txt). Using certifi.", flush=True)
        return False

    truststore.inject_into_ssl()
    print(f"DEV: {ENV_VAR}=1, HTTPS now uses the OS certificate store.", flush=True)
    return True
