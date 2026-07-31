# notifications.py
# Push-notification token registry + super-admin broadcast system for BookMyConsole.
#
# Delivery is via Expo's push notification service (https://exp.host/--/api/v2/push/send) —
# the mobile app registers its Expo push token once logged in, and this module fans a
# broadcast out to every registered token in batches (Expo's API caps a single request
# at 100 messages).

import requests
from datetime import datetime, timezone, timedelta
from bson.objectid import ObjectId
from .db_connection import db_main

IST = timezone(timedelta(hours=5, minutes=30))
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_PUSH_BATCH_SIZE = 100


def register_push_token_handler(user_email: str, expo_push_token: str, platform: str = None):
    """Upserts the calling user's Expo push token. One token per user — a fresh login on a
    new device just overwrites the old one, since Expo tokens aren't meaningfully
    multi-device-aware without also tracking per-device identity, which nothing here needs."""
    try:
        if not user_email or not expo_push_token:
            return {"status": "error", "message": "user_email and expo_push_token are required."}, 400
        if not expo_push_token.startswith("ExponentPushToken[") and not expo_push_token.startswith("ExpoPushToken["):
            return {"status": "error", "message": "That doesn't look like a valid Expo push token."}, 400

        db_main.push_tokens.update_one(
            {"user_email": user_email},
            {"$set": {
                "user_email": user_email,
                "expo_push_token": expo_push_token,
                "platform": platform,
                "updated_at": datetime.now(IST),
            }},
            upsert=True,
        )
        return {"status": "success", "message": "Push token registered."}, 200
    except Exception as e:
        return {"status": "error", "message": f"Failed to register push token: {e}"}, 500


def _send_expo_push_batch(tokens: list, title: str, body: str) -> int:
    """Sends one batch (<=100) of push messages via Expo's push API. Returns how many of
    the batch Expo accepted (status == 'ok') — a token can independently fail (e.g.
    DeviceNotRegistered for an uninstalled app) without failing the whole batch."""
    if not tokens:
        return 0
    messages = [{"to": t, "title": title, "body": body, "sound": "default"} for t in tokens]
    try:
        response = requests.post(
            EXPO_PUSH_URL,
            headers={"accept": "application/json", "content-type": "application/json"},
            json=messages,
            timeout=15,
        )
        if response.status_code >= 400:
            print(f"[PUSH] Expo API error {response.status_code}: {response.text}")
            return 0
        data = response.json().get("data", [])
        accepted = sum(1 for r in data if isinstance(r, dict) and r.get("status") == "ok")
        return accepted
    except requests.RequestException as e:
        print(f"[PUSH] Expo push request failed: {e}")
        return 0


def send_broadcast_notification_handler(title: str, body: str, audience: str, sent_by: str, channel: str = "push"):
    """Super-admin broadcast. Currently only 'all_users' (every gamer with a registered
    push token) + the 'push' channel are implemented — 'all_cafes'/'specific' audiences
    and the 'email' channel are rejected rather than silently sending to nobody, since
    the compose UI offers them but nothing backs them yet."""
    try:
        if not title or not body:
            return {"status": "error", "message": "Title and message are required."}, 400
        if audience != "all_users":
            return {
                "status": "error",
                "message": f"Audience '{audience}' isn't supported yet — only 'all_users' is currently wired up.",
            }, 400
        if channel != "push":
            return {
                "status": "error",
                "message": f"Channel '{channel}' isn't supported yet — only 'push' is currently wired up.",
            }, 400

        tokens = [
            doc["expo_push_token"]
            for doc in db_main.push_tokens.find({}, {"expo_push_token": 1})
            if doc.get("expo_push_token")
        ]

        accepted_count = 0
        for i in range(0, len(tokens), EXPO_PUSH_BATCH_SIZE):
            batch = tokens[i:i + EXPO_PUSH_BATCH_SIZE]
            accepted_count += _send_expo_push_batch(batch, title, body)

        record = {
            "title": title,
            "body": body,
            "audience": audience,
            "channel": "push",
            "sent_by": sent_by,
            "recipient_count": len(tokens),
            "accepted_count": accepted_count,
            "created_at": datetime.now(IST),
        }
        result = db_main.notifications.insert_one(record)
        # insert_one mutates `record` in place, adding a raw ObjectId `_id` key - drop
        # it (mirroring list_broadcasts_handler's .pop("_id")) since ObjectId isn't
        # JSON-serializable and would otherwise crash DRF's response rendering with an
        # unhandled 500 even though this function itself returns successfully.
        record.pop("_id", None)
        record["id"] = str(result.inserted_id)
        record["created_at"] = record["created_at"].isoformat()

        return {
            "status": "success",
            "message": f"Sent to {accepted_count} of {len(tokens)} registered device(s).",
            "notification": record,
        }, 200
    except Exception as e:
        return {"status": "error", "message": f"Failed to send broadcast: {e}"}, 500


def list_broadcasts_handler(limit: int = 50):
    """Recent broadcast history for the super-admin panel's 'Recent broadcasts' list."""
    try:
        docs = list(db_main.notifications.find().sort("created_at", -1).limit(limit))
        for d in docs:
            d["id"] = str(d.pop("_id"))
            if isinstance(d.get("created_at"), datetime):
                d["created_at"] = d["created_at"].isoformat()
        return {"status": "success", "notifications": docs}, 200
    except Exception as e:
        return {"status": "error", "message": f"Failed to list broadcasts: {e}"}, 500
