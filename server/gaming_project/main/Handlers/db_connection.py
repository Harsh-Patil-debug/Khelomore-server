# db_connection.py
# MongoDB connection for KheloMore Gaming Hub
# ─────────────────────────────────────────────

import os
import pymongo
from dotenv import load_dotenv
from pymongo.errors import ConnectionFailure, ConfigurationError

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "KheloMoreDB")

try:
    client = pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db_main = client[MONGO_DB_NAME]
    print(f"[KheloMore] MongoDB connected - database: '{MONGO_DB_NAME}'")
except (ConnectionFailure, ConfigurationError, Exception) as e:
    print(f"[KheloMore] Failed to connect to MongoDB: {e}")
    client = None
    db_main = None

# ─── Collections ──────────────────────────────────────────────────────────────
# db_main.users          → { _id, email, password_hash, first_name, last_name, phone_number, profile_picture, status, created_at }
# db_main.cafes          → { _id, name, location, address, amenities, price_per_hour, images, available_slots, is_active }
# db_main.bookings       → { _id, user_id, cafe_id, date, slots[], total_price, status, created_at }
# db_main.notifications  → { _id, title, body, user_ids[], is_broadcast, created_at }
# db_main.push_tokens    → { _id, user_id, token, platform, created_at }