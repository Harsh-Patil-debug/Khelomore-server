# tournaments.py
# Handlers for managing esports tournaments in KheloMore Gaming Hub

import os
import json
import traceback
from datetime import datetime, timezone
import cloudinary
import cloudinary.uploader
from bson import ObjectId
from .db_connection import get_db

# Configure Cloudinary
cloudinary_secret = os.getenv("CLOUDINARY_API_SECRET")
if not cloudinary_secret or cloudinary_secret == "your_api_secret_placeholder":
    print("[KheloMore Warning] CLOUDINARY_API_SECRET environment variable is not set. Image uploads to Cloudinary will fail and fallback to default images.")
    cloudinary_secret = "your_api_secret_placeholder"

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "dghp9tq9m"),
    api_key=os.getenv("CLOUDINARY_API_KEY", "631388716584283"),
    api_secret=cloudinary_secret,
    secure=True
)

def safe_object_id(id_str):
    """Safely converts a string to an ObjectId if valid, otherwise returns the original string."""
    try:
        return ObjectId(id_str)
    except Exception:
        return id_str


SEED_TOURNAMENTS = [
    {
        "_id": "6a3bb8f1ec5f66ea71485bd1",
        "game": "VALORANT",
        "title": "Cyber Invitational S3",
        "prize": "\u20b950,000",
        "entry": "Paid Entry",
        "entry_fee": 400,
        "registered": 28,
        "capacity": 32,
        "unit": "Squads",
        "mode": "Squad",
        "starts": "Sat 28 Jun \u00b7 6:00 PM",
        "starts_iso": "2026-06-28T12:30:00Z",
        "registration_open": True,
        "images": [
            "https://images.unsplash.com/photo-1624138784614-87fd1b6528f8?q=80&w=600"
        ]
    },
    {
        "_id": "6a3bb8f1ec5f66ea71485bd2",
        "game": "BGMI",
        "title": "Nerul Battle Royale",
        "prize": "\u20b925,000",
        "entry": "Free Entry",
        "registered": 56,
        "capacity": 64,
        "unit": "Squads",
        "mode": "Squad",
        "starts": "Sun 29 Jun \u00b7 4:00 PM",
        "starts_iso": "2026-06-29T10:30:00Z",
        "registration_open": True,
        "images": [
            "https://images.unsplash.com/photo-1607604276583-eef5d076aa5f?q=80&w=600"
        ]
    },
    {
        "_id": "6a3bb8f1ec5f66ea71485bd3",
        "game": "CS2",
        "title": "Clutch Cup Mumbai",
        "prize": "\u20b91,00,000",
        "entry": "Paid Entry",
        "entry_fee": 600,
        "registered": 12,
        "capacity": 16,
        "unit": "Squads",
        "mode": "Squad",
        "starts": "Fri 04 Jul \u00b7 7:30 PM",
        "starts_iso": "2026-07-04T14:00:00Z",
        "registration_open": True,
        "images": [
            "https://images.unsplash.com/photo-1538481199705-c710c4e965fc?q=80&w=600"
        ]
    },
    {
        "_id": "6a3bb8f1ec5f66ea71485bd4",
        "game": "TEKKEN 8",
        "title": "Fight Night Solo",
        "prize": "\u20b910,000",
        "entry": "Free Entry",
        "registered": 22,
        "capacity": 32,
        "unit": "Players",
        "mode": "Solo",
        "starts": "Wed 02 Jul \u00b7 8:00 PM",
        "starts_iso": "2026-07-02T14:30:00Z",
        "registration_open": True,
        "images": [
            "https://images.unsplash.com/photo-1551103782-8ab07afd45c1?q=80&w=600"
        ]
    }
]



def compute_is_live(starts_iso):
    """Returns True if the tournament start time has passed (tournament is now LIVE)."""
    if not starts_iso:
        return False
    try:
        start_dt = datetime.fromisoformat(starts_iso.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) >= start_dt
    except Exception:
        return False


def map_tournament_doc(doc):
    """Maps a MongoDB tournament document to the format expected by the frontend."""
    starts_iso = doc.get("starts_iso")
    is_live = compute_is_live(starts_iso)

    # registrationOpen is False if admin explicitly closed it OR if tournament went live
    db_registration_open = doc.get("registration_open", True)
    effective_registration_open = db_registration_open and not is_live

    return {
        "id": str(doc["_id"]),
        "game": doc.get("game", ""),
        "title": doc.get("title", ""),
        "prize": doc.get("prize", ""),
        "entry": doc.get("entry", "Free Entry"),
        "entryFee": int(doc.get("entry_fee")) if doc.get("entry_fee") is not None else None,
        "registered": int(doc.get("registered", 0)),
        "capacity": int(doc.get("capacity", 32)),
        "unit": doc.get("unit", "Squads"),
        "mode": doc.get("mode", "Squad"),
        "starts": doc.get("starts", ""),
        "startsIso": starts_iso,
        "isLive": is_live,
        "registrationOpen": effective_registration_open,
        "images": doc.get("images", [])
    }


def get_tournaments_handler():
    """Retrieves all esports tournaments from the database. Seeds if empty."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        generic_img = "https://images.unsplash.com/photo-1542751371-adc38448a05e?q=80&w=600"
        seeded_titles = [t["title"] for t in SEED_TOURNAMENTS]

        # Synced ObjectId list for seeded tournaments
        new_ids = [
            ObjectId("6a3bb8f1ec5f66ea71485bd1"), # VALORANT
            ObjectId("6a3bb8f1ec5f66ea71485bd2"), # BGMI
            ObjectId("6a3bb8f1ec5f66ea71485bd3"), # CS2
            ObjectId("6a3bb8f1ec5f66ea71485bd4")  # TEKKEN 8
        ]

        # Clean up old seed tournaments (ObjectId or old string IDs)
        db_main.tournaments.delete_many({
            "_id": {"$in": ["val-invitational", "bgmi-rumble", "cs2-clutch", "tekken-solo", "tekken 8"]}
        })
        for seed in SEED_TOURNAMENTS:
            db_main.tournaments.delete_many({
                "title": seed["title"],
                "_id": {"$nin": new_ids}
            })

        # Migrate legacy registrations to the new ObjectId formats
        db_main.registrations.update_many(
            {"tournament_id": {"$in": ["tekken-solo", "tekken 8"]}},
            {"$set": {"tournament_id": ObjectId("6a3bb8f1ec5f66ea71485bd4")}}
        )
        db_main.registrations.update_many(
            {"tournament_id": "val-invitational"},
            {"$set": {"tournament_id": ObjectId("6a3bb8f1ec5f66ea71485bd1")}}
        )
        db_main.registrations.update_many(
            {"tournament_id": "bgmi-rumble"},
            {"$set": {"tournament_id": ObjectId("6a3bb8f1ec5f66ea71485bd2")}}
        )
        db_main.registrations.update_many(
            {"tournament_id": "cs2-clutch"},
            {"$set": {"tournament_id": ObjectId("6a3bb8f1ec5f66ea71485bd3")}}
        )

        delete_result = db_main.tournaments.delete_many({
            "title": {"$in": seeded_titles},
            "images": generic_img
        })
        if delete_result.deleted_count > 0:
            print(f"[KheloMore] Cleared {delete_result.deleted_count} stale generic seeded tournaments.")

        for seed in SEED_TOURNAMENTS:
            seed_copy = dict(seed)
            seed_copy["_id"] = ObjectId(seed_copy["_id"])
            if db_main.tournaments.count_documents({"_id": seed_copy["_id"]}) == 0:
                db_main.tournaments.insert_one(seed_copy)
                print(f"[KheloMore] Seeded default tournament: '{seed_copy['title']}' with ID '{seed_copy['_id']}'")
            else:
                # Migrate existing seeds that are missing starts_iso / registration_open
                db_main.tournaments.update_many(
                    {"_id": seed_copy["_id"], "starts_iso": {"$exists": False}},
                    {"$set": {
                        "starts_iso": seed_copy["starts_iso"],
                        "registration_open": seed_copy.get("registration_open", True)
                    }}
                )

        # Migrate any user-created tournaments missing registration_open
        db_main.tournaments.update_many(
            {"registration_open": {"$exists": False}},
            {"$set": {"registration_open": True}}
        )

        docs = list(db_main.tournaments.find({}))
        mapped = [map_tournament_doc(d) for d in docs]

        return {"status": "success", "tournaments": mapped}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve tournaments: {e}"}


def toggle_registration_handler(tournament_id):
    """Admin: toggles the registration_open flag for a specific tournament."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        oid = safe_object_id(tournament_id)
        doc = db_main.tournaments.find_one({"_id": oid})
        if not doc:
            return {"status": "error", "message": "Tournament not found."}

        new_value = not doc.get("registration_open", True)
        db_main.tournaments.update_one({"_id": oid}, {"$set": {"registration_open": new_value}})
        doc["registration_open"] = new_value

        action = "opened" if new_value else "closed"
        print(f"[KheloMore] Admin {action} registration for: '{doc.get('title')}'")

        return {"status": "success", "tournament": map_tournament_doc(doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to toggle registration: {e}"}



def create_tournament_handler(data, files=None):
    """Creates a new tournament in the database, uploading cover image to Cloudinary."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        game = data.get("game")
        title = data.get("title")
        prize = data.get("prize")
        entry = data.get("entry", "Free Entry")
        entry_fee = data.get("entryFee")
        capacity = data.get("capacity")
        unit = data.get("unit", "Squads")
        mode = data.get("mode", "Squad")
        starts = data.get("starts")
        starts_iso = data.get("startsIso")
        images = data.get("images", [])

        if not game or not title or not prize or not starts or capacity is None:
            return {
                "status": "error",
                "message": "Game, Title, Prize Pool, Starts At, and Capacity are required fields."
            }

        try:
            capacity = int(capacity)
        except (ValueError, TypeError):
            return {"status": "error", "message": "Capacity must be an integer."}

        if entry == "Paid Entry":
            try:
                entry_fee = int(entry_fee) if entry_fee is not None else 0
            except (ValueError, TypeError):
                return {"status": "error", "message": "Entry Fee must be an integer."}
        else:
            entry_fee = None

        # Cloudinary upload
        image_url = None
        if files and "image" in files:
            image_file = files["image"]
            try:
                upload_result = cloudinary.uploader.upload(image_file)
                image_url = upload_result.get("secure_url")
                print(f"[Cloudinary] Successfully uploaded tournament image to: {image_url}")
            except Exception as upload_err:
                print(f"[Cloudinary] Tournament image upload failed: {upload_err}")
                print(traceback.format_exc())

        final_images = []
        if image_url:
            final_images.append(image_url)

        if not final_images:
            if isinstance(images, list) and len(images) > 0:
                final_images = images
            elif isinstance(images, str) and images.strip():
                try:
                    parsed_images = json.loads(images)
                    if isinstance(parsed_images, list):
                        final_images = parsed_images
                    else:
                        final_images = [str(parsed_images)]
                except Exception:
                    final_images = [images.strip()]
            else:
                final_images = ["https://images.unsplash.com/photo-1542751371-adc38448a05e?q=80&w=600"]

        tournament_doc = {
            "game": game,
            "title": title,
            "prize": prize,
            "entry": entry,
            "entry_fee": entry_fee,
            "registered": 0,
            "capacity": capacity,
            "unit": unit,
            "mode": mode,
            "starts": starts,
            "starts_iso": starts_iso,
            "registration_open": True,
            "images": final_images
        }

        result = db_main.tournaments.insert_one(tournament_doc)
        tournament_doc["_id"] = result.inserted_id

        return {"status": "success", "tournament": map_tournament_doc(tournament_doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to create tournament: {e}"}


def register_tournament_handler(tournament_id, data):
    """Registers a squad/gamer for a tournament, increments slot/registered count, and closes registration if capacity is reached."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        oid = safe_object_id(tournament_id)
        tournament = db_main.tournaments.find_one({"_id": oid})
        if not tournament:
            return {"status": "error", "message": "Tournament not found."}

        # Check if tournament registration is open and not live
        starts_iso = tournament.get("starts_iso")
        is_live = compute_is_live(starts_iso)
        if is_live:
            return {"status": "error", "message": "Tournament has already started."}

        if not tournament.get("registration_open", True):
            return {"status": "error", "message": "Registrations are closed for this tournament."}

        # Check capacity
        registered = int(tournament.get("registered", 0))
        capacity = int(tournament.get("capacity", 32))
        if registered >= capacity:
            return {"status": "error", "message": "Tournament is already full."}

        gamer_ids = data.get("gamer_ids", [])
        if not gamer_ids or not isinstance(gamer_ids, list):
            return {"status": "error", "message": "Gamer IDs are required and must be a list."}

        # Store registration info in database
        registration_doc = {
            "tournament_id": oid,
            "gamer_ids": gamer_ids,
            "registered_at": datetime.now(timezone.utc)
        }
        db_main.registrations.insert_one(registration_doc)

        # Increment registered slots
        new_registered = registered + 1
        update_fields = {"registered": new_registered}

        # If capacity is reached, automatically close registration
        if new_registered >= capacity:
            update_fields["registration_open"] = False

        db_main.tournaments.update_one({"_id": oid}, {"$set": update_fields})

        # Fetch and return the updated tournament doc
        updated_tournament = db_main.tournaments.find_one({"_id": oid})
        return {"status": "success", "tournament": map_tournament_doc(updated_tournament)}

    except Exception as e:
        print(f"[KheloMore] Failed to register for tournament: {e}")
        print(traceback.format_exc())
        return {"status": "error", "message": f"Failed to register: {e}"}

