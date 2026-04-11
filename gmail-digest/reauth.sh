#!/usr/bin/env bash
# Re-authorize Google OAuth for gmail-digest on a headless VM.
#
# Usage:
#   1. From your LAPTOP, open an SSH tunnel that forwards port 8080:
#          ssh -L 8080:localhost:8080 root@<hetzner-ip>
#   2. On the VM, run this script:
#          cd /root/workspace/sanek325/gmail-digest && ./reauth.sh
#   3. Open the URL it prints in your laptop browser and approve access.
#      Google will redirect to http://localhost:8080/ which tunnels back here.
#
# Scopes: gmail.readonly
set -euo pipefail

cd "$(dirname "$0")"
rm -f token.json

PYTHONUNBUFFERED=1 .venv/bin/python -u - <<'PY'
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
creds = flow.run_local_server(
    host="localhost",
    port=8080,
    open_browser=False,
    authorization_prompt_message="Open this URL in your laptop browser:\n\n  {url}\n",
    success_message="Authorization complete. You may close this tab.",
)
with open("token.json", "w") as f:
    f.write(creds.to_json())
print("Saved token.json")
PY
