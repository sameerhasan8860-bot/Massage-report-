import os

API_ID       = int(os.environ["API_ID"])
API_HASH     = os.environ["API_HASH"]
BOT_TOKEN    = os.environ["BOT_TOKEN"]
MONGO_URI    = os.environ["MONGO_URI"]\nOWNER_ID     = int(os.environ["OWNER_ID"])\nOWNER_USERNAME = os.environ.get("OWNER_USERNAME", "").strip()

PROXY_ROTATE_INTERVAL = 1  # seconds
