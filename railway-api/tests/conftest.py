import os
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
for key in ('DATABASE_URL','DISCORD_CLIENT_ID','DISCORD_CLIENT_SECRET','BOT_INTERNAL_TOKEN','OAUTH_STATE_SECRET'):
    os.environ[key]='test-only-placeholder'
