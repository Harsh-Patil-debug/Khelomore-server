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
from .upload_validation import validate_image_upload

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



def map_tournament_doc(doc):
    """Maps a MongoDB tournament document to the format expected by the frontend."""
    starts_iso = doc.get("starts_iso")
    status = doc.get("status", "upcoming")
    is_live = (status == "live")

    # registrationOpen is True ONLY if status is upcoming and registration_open flag in MongoDB is True
    db_registration_open = doc.get("registration_open", True)
    effective_registration_open = db_registration_open and (status == "upcoming")

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
        "images": doc.get("images", []),
        "cafe_id": doc.get("cafe_id"),
        "status": status
    }


def get_tournaments_handler(cafe_id=None):
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

        query = {}
        if cafe_id:
            query["$or"] = [
                {"cafe_id": cafe_id},
                {"cafe_id": None},
                {"cafe_id": {"$exists": False}}
            ]

        docs = list(db_main.tournaments.find(query))
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
            validation_error = validate_image_upload(image_file)
            if validation_error:
                return {"status": "error", "message": validation_error}
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
            "images": final_images,
            "cafe_id": data.get("cafe_id") or data.get("cafeId"),
            "status": "upcoming"
        }

        result = db_main.tournaments.insert_one(tournament_doc)
        tournament_doc["_id"] = result.inserted_id

        return {"status": "success", "tournament": map_tournament_doc(tournament_doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to create tournament: {e}"}


def register_tournament_handler(tournament_id, user_email, data):
    """Registers a squad/gamer for a tournament, increments slot/registered count, and closes registration if capacity is reached."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        oid = safe_object_id(tournament_id)
        tournament = db_main.tournaments.find_one({"_id": oid})
        if not tournament:
            return {"status": "error", "message": "Tournament not found."}

        # Check if tournament is live (status set manually by admin)
        is_live = tournament.get("status", "upcoming") == "live"
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

        # Entry fee is read from the tournament document (server-side/admin-set) — never trust
        # a client-supplied amount. A paid tournament requires a verified Razorpay payment.
        entry_fee = tournament.get("entry_fee") or 0
        is_paid_entry = tournament.get("entry") == "Paid Entry" and int(entry_fee) > 0
        if is_paid_entry:
            from .payments import verify_razorpay_payment
            razorpay_order_id = data.get("razorpay_order_id")
            razorpay_payment_id = data.get("razorpay_payment_id")
            razorpay_signature = data.get("razorpay_signature")
            if not verify_razorpay_payment(razorpay_order_id, razorpay_payment_id, razorpay_signature, int(entry_fee) * 100):
                return {"status": "error", "message": "Payment verification failed. Please complete payment before registering."}

        # Store registration info in database
        registration_doc = {
            "tournament_id": oid,
            "tournament_title": tournament.get("title", "Unknown Tournament"),
            "user_email": user_email.strip().lower() if user_email else None,
            "gamer_ids": gamer_ids,
            "amount_paid": int(entry_fee) if is_paid_entry else 0,
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


def get_user_registrations_handler(user_email: str):
    """
    Fetches the tournament IDs that the user has registered for from MongoDB.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
        
    try:
        user_email = user_email.strip().lower()
        # Find all registrations under this email
        regs = db_main.registrations.find({"user_email": user_email})
        tournament_ids = []
        for r in regs:
            t_id = r.get("tournament_id")
            if t_id:
                tournament_ids.append(str(t_id))
                
        return {
            "status": "success",
            "registrations": list(set(tournament_ids))
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve registrations: {str(e)}"
        }, 500


def update_tournament_handler(tournament_id, data, files=None):
    """Updates an existing tournament in the database, with optional new cover image upload to Cloudinary."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        oid = safe_object_id(tournament_id)
        existing = db_main.tournaments.find_one({"_id": oid})
        if not existing:
            return {"status": "error", "message": "Tournament not found."}

        # Fields mapping
        game = data.get("game")
        title = data.get("title")
        prize = data.get("prize")
        entry = data.get("entry")
        entry_fee = data.get("entryFee")
        capacity = data.get("capacity")
        unit = data.get("unit")
        mode = data.get("mode")
        starts = data.get("starts")
        starts_iso = data.get("startsIso")
        status = data.get("status")

        update_doc = {}

        if game is not None: update_doc["game"] = game
        if title is not None: update_doc["title"] = title
        if prize is not None: update_doc["prize"] = prize
        if entry is not None: update_doc["entry"] = entry
        if status is not None:
            update_doc["status"] = status
            if status == "cancelled":
                update_doc["registration_open"] = False
        
        if capacity is not None:
            try:
                update_doc["capacity"] = int(capacity)
            except (ValueError, TypeError):
                return {"status": "error", "message": "Capacity must be an integer."}

        if entry == "Paid Entry" or (entry is None and existing.get("entry") == "Paid Entry"):
            if entry_fee is not None:
                try:
                    update_doc["entry_fee"] = int(entry_fee)
                except (ValueError, TypeError):
                    return {"status": "error", "message": "Entry Fee must be an integer."}
        elif entry == "Free Entry":
            update_doc["entry_fee"] = None

        if unit is not None: update_doc["unit"] = unit
        if mode is not None: update_doc["mode"] = mode
        if starts is not None: update_doc["starts"] = starts
        if starts_iso is not None: update_doc["starts_iso"] = starts_iso

        # Handle Cloudinary upload if a new file is sent
        image_url = None
        if files and "image" in files:
            image_file = files["image"]
            validation_error = validate_image_upload(image_file)
            if validation_error:
                return {"status": "error", "message": validation_error}
            try:
                upload_result = cloudinary.uploader.upload(image_file)
                image_url = upload_result.get("secure_url")
                print(f"[Cloudinary] Successfully updated tournament image to: {image_url}")
            except Exception as upload_err:
                print(f"[Cloudinary] Tournament image update failed: {upload_err}")
                print(traceback.format_exc())

        if image_url:
            update_doc["images"] = [image_url]

        if update_doc:
            db_main.tournaments.update_one({"_id": oid}, {"$set": update_doc})
        
        # Retrieve the updated document
        updated_doc = db_main.tournaments.find_one({"_id": oid})
        return {"status": "success", "tournament": map_tournament_doc(updated_doc)}

    except Exception as e:
        print(f"[KheloMore] Failed to update tournament: {e}")
        print(traceback.format_exc())
        return {"status": "error", "message": f"Failed to update tournament: {e}"}


def delete_tournament_handler(tournament_id):
    """Deletes a tournament and all of its player/team registrations from MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        oid = safe_object_id(tournament_id)
        # Check if exists
        existing = db_main.tournaments.find_one({"_id": oid})
        if not existing:
            return {"status": "error", "message": "Tournament not found."}

        # Delete the tournament
        db_main.tournaments.delete_one({"_id": oid})

        # Delete all registrations referencing this tournament
        delete_regs = db_main.registrations.delete_many({"tournament_id": oid})
        print(f"[KheloMore] Deleted tournament '{existing.get('title')}' and cleared {delete_regs.deleted_count} registrations.")

        return {"status": "success", "message": "Tournament and associated registrations deleted successfully."}

    except Exception as e:
        print(f"[KheloMore] Failed to delete tournament: {e}")
        print(traceback.format_exc())
        return {"status": "error", "message": f"Failed to delete tournament: {e}"}


def get_tournament_registrations_handler(tournament_id):
    """
    Fetches all registrations for a given tournament (admin view).
    Joins user email to profile name via the users collection.
    Attaches entry_fee and mode from the parent tournament document.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}, 500

    try:
        oid = safe_object_id(tournament_id)
        tournament = db_main.tournaments.find_one({"_id": oid})
        if not tournament:
            return {"status": "error", "message": "Tournament not found."}, 404

        entry_fee = tournament.get("entry_fee") or 0
        entry_type = tournament.get("entry", "Free Entry")
        mode = tournament.get("mode", "Squad")
        is_paid = entry_type == "Paid Entry" and int(entry_fee) > 0

        # Fetch all registrations for this tournament
        regs_list = list(db_main.registrations.find({"tournament_id": oid}).sort("registered_at", 1))

        results = []
        for reg in regs_list:
            user_email = reg.get("user_email", "")
            gamer_ids = reg.get("gamer_ids", [])
            registered_at = reg.get("registered_at")

            # Try to look up display name from users collection
            display_name = user_email
            if user_email:
                user_doc = db_main.users.find_one({"email": user_email})
                if user_doc:
                    display_name = (
                        user_doc.get("full_name")
                        or user_doc.get("gamertag")
                        or user_doc.get("name")
                        or user_email
                    )

            results.append({
                "id": str(reg.get("_id", "")),
                "user_email": user_email,
                "display_name": display_name,
                "gamer_ids": gamer_ids,
                "registered_at": (registered_at.isoformat() + "Z") if registered_at else None,
                "amount_paid": int(entry_fee) if is_paid else 0,
                "is_paid": is_paid,
                "mode": mode,
            })

        return {
            "status": "success",
            "tournament_title": tournament.get("title", ""),
            "mode": mode,
            "entry_type": entry_type,
            "entry_fee": int(entry_fee) if entry_fee else 0,
            "registrations": results,
            "total": len(results),
        }, 200

    except Exception as e:
        print(f"[KheloMore] Failed to fetch tournament registrations: {e}")
        print(traceback.format_exc())
        return {"status": "error", "message": f"Failed to fetch registrations: {e}"}, 500
