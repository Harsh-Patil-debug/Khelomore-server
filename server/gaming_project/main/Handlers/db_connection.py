# db_connection.py
# MongoDB connection for KheloMore Gaming Hub
# ─────────────────────────────────────────────

import os
import pymongo
from pathlib import Path
from dotenv import load_dotenv

# Ensure .env is loaded using absolute path
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(BASE_DIR / '.env')

MONGO_URL = os.getenv("MONGO_URL")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "KheloMoreDB")

_client = None

def get_db():
    global _client
    if _client is None:
        try:
            _client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
            # Ping database to force connection check
            _client.admin.command('ping')
            print(f"[KheloMore] MongoDB connected successfully - database: '{MONGO_DB_NAME}'")
        except Exception as e:
            print(f"[KheloMore] Failed to connect to MongoDB: {e}")
            _client = None
            return None
    return _client[MONGO_DB_NAME]