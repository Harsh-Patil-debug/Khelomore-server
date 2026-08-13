# tournaments.py
# Handlers for managing esports tournaments in BookMyConsole Gaming Hub

import os
import json
import traceback
from datetime import datetime, timezone
import cloudinary
import cloudinary.uploader
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from .db_connection import get_db
from .upload_validation import validate_image_upload
from . import input_validation

_registrations_index_ensured = False


def _ensure_registrations_index(db_main):
    """Lazily creates the unique index that actually guarantees one registration per
    (tournament_id, user_email) — the find_one check in register_tournament_handler is
    just a fast-fail UX nicety and races on its own: two concurrent requests from the
    same user (a double-tap, a network retry) can both pass that read before either
    commits its insert. This index is what makes the second insert fail atomically at
    the database level instead of silently succeeding twice."""
    global _registrations_index_ensured
    if not _registrations_index_ensured:
        try:
            db_main.registrations.create_index(
                [("tournament_id", 1), ("user_email", 1)], unique=True
            )
        except Exception:
            pass
        _registrations_index_ensured = True

# Configure Cloudinary
cloudinary_secret = os.getenv("CLOUDINARY_API_SECRET")
if not cloudinary_secret or cloudinary_secret == "your_api_secret_placeholder":
    print("[BookMyConsole Warning] CLOUDINARY_API_SECRET environment variable is not set. Image uploads to Cloudinary will fail and fallback to default images.")
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


def map_tournament_doc(doc, cafe_name=None):
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
        # None for platform-wide tournaments (no cafe_id) - the frontend shows a
        # "BookMyConsole" / platform badge in that case instead of a cafe name.
        "cafe_name": cafe_name,
        "status": status
    }


def _resolve_cafe_name(db_main, cafe_id):
    """Looks up a single cafe's display name for one tournament doc (create/update/etc)."""
    if not cafe_id:
        return None
    try:
        cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)}, {"name": 1})
        return cafe.get("name") if cafe else None
    except Exception:
        return None


def _resolve_cafe_names_batch(db_main, cafe_ids):
    """Batch-resolves many tournaments' cafe_ids to names in one query (used by the list
    endpoint) instead of one lookup per tournament."""
    valid_ids = []
    for cid in cafe_ids:
        if not cid:
            continue
        try:
            valid_ids.append(ObjectId(cid))
        except Exception:
            continue
    if not valid_ids:
        return {}
    cafes = db_main.cafes.find({"_id": {"$in": valid_ids}}, {"name": 1})
    return {str(c["_id"]): c.get("name") for c in cafes}


def get_tournaments_handler(cafe_id=None):
    """Retrieves all esports tournaments from the database."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        # Migrate any user-created tournaments missing registration_open — the one
        # ongoing backfill that's actually still relevant (real tournaments, not seeds).
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
        cafe_names_by_id = _resolve_cafe_names_batch(db_main, [d.get("cafe_id") for d in docs])
        mapped = [map_tournament_doc(d, cafe_name=cafe_names_by_id.get(d.get("cafe_id"))) for d in docs]

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
        print(f"[BookMyConsole] Admin {action} registration for: '{doc.get('title')}'")

        cafe_name = _resolve_cafe_name(db_main, doc.get("cafe_id"))
        return {"status": "success", "tournament": map_tournament_doc(doc, cafe_name=cafe_name)}
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

        validation_error = (
            input_validation.validate_text(game, "Game", max_len=60)
            or input_validation.validate_text(title, "Title", max_len=150)
            or input_validation.validate_text(prize, "Prize Pool", max_len=100)
        )
        if validation_error:
            return {"status": "error", "message": validation_error}

        capacity, capacity_error = input_validation.parse_bounded_number(capacity, "Capacity", min_val=1, max_val=100000)
        if capacity_error:
            return {"status": "error", "message": capacity_error}

        if entry == "Paid Entry":
            entry_fee, fee_error = input_validation.parse_bounded_number(
                entry_fee if entry_fee is not None else 0, "Entry Fee", min_val=0, max_val=1000000
            )
            if fee_error:
                return {"status": "error", "message": fee_error}
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

        cafe_name = _resolve_cafe_name(db_main, tournament_doc.get("cafe_id"))
        return {"status": "success", "tournament": map_tournament_doc(tournament_doc, cafe_name=cafe_name)}
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

        _ensure_registrations_index(db_main)
        cleaned_email = (user_email or "").strip().lower()
        if cleaned_email and db_main.registrations.find_one({"tournament_id": oid, "user_email": cleaned_email}):
            return {"status": "error", "message": "You've already registered for this tournament."}

        # Check capacity
        registered = int(tournament.get("registered", 0))
        capacity = int(tournament.get("capacity", 32))
        if registered >= capacity:
            return {"status": "error", "message": "Tournament is already full."}

        gamer_ids = data.get("gamer_ids", [])
        if not gamer_ids or not isinstance(gamer_ids, list):
            return {"status": "error", "message": "Gamer IDs are required and must be a list."}

        # SECURITY/INTEGRITY: the client-side registration form gates submission on every
        # slot being filled with a real-looking ID, but that's only ever enforced in the
        # browser — a direct API call could previously send a single ID for a 4-player
        # squad, blank/whitespace-only entries, duplicate IDs across slots, or an absurdly
        # long string in any slot.
        expected_team_size = 4 if tournament.get("mode", "Squad") == "Squad" else 1
        if len(gamer_ids) != expected_team_size:
            return {"status": "error", "message": f"This tournament requires exactly {expected_team_size} gamer ID(s)."}
        cleaned_ids = []
        for gid in gamer_ids:
            if not isinstance(gid, str) or not gid.strip():
                return {"status": "error", "message": "Every gamer ID slot must be filled in."}
            if len(gid) > 50:
                return {"status": "error", "message": "Gamer ID is too long."}
            cleaned_ids.append(gid.strip())
        if len(set(cleaned_ids)) != len(cleaned_ids):
            return {"status": "error", "message": "Duplicate gamer ID entered — each player must be unique."}
        gamer_ids = cleaned_ids

        # Entry fee is read from the tournament document (server-side/admin-set) — never trust
        # a client-supplied amount. A paid tournament requires a verified Cashfree payment.
        entry_fee = tournament.get("entry_fee") or 0
        is_paid_entry = tournament.get("entry") == "Paid Entry" and int(entry_fee) > 0
        payment_settlement = None
        if is_paid_entry:
            from .payments import verify_cashfree_payment
            cashfree_order_id = data.get("cashfree_order_id")
            tournament_cafe_id = tournament.get("cafe_id")
            if not verify_cashfree_payment(cashfree_order_id, int(entry_fee) * 100, cafe_id=tournament_cafe_id):
                return {"status": "error", "message": "Payment verification failed. Please complete payment before registering."}
            if tournament_cafe_id:
                order_record = db_main.cafe_payment_orders.find_one({"order_id": cashfree_order_id, "cafe_id": tournament_cafe_id})
                payment_settlement = "platform_pending_payout" if (order_record and order_record.get("used_platform_fallback")) else "direct_to_cafe"

        # Store registration info in database
        registration_doc = {
            "tournament_id": oid,
            "tournament_title": tournament.get("title", "Unknown Tournament"),
            "user_email": user_email.strip().lower() if user_email else None,
            "gamer_ids": gamer_ids,
            "amount_paid": int(entry_fee) if is_paid_entry else 0,
            "payment_settlement": payment_settlement,
            "registered_at": datetime.now(timezone.utc)
        }
        try:
            db_main.registrations.insert_one(registration_doc)
        except DuplicateKeyError:
            # Lost the race: another request for this same (tournament_id, user_email)
            # committed first, between the find_one check above and this insert. For a
            # free tournament this is a harmless no-op rejection. For a paid one, the
            # payment was already verified and claimed (verify_cashfree_payment's replay
            # protection) before this insert ever ran — that money is real and already
            # captured, so this is the same residual "contact support for a refund" gap
            # as the equivalent slot-booking race, not something silently fixed here.
            message = "You've already registered for this tournament."
            if is_paid_entry:
                message = (
                    "You've already registered for this tournament. Your payment was "
                    "captured — contact support for a refund if this was unexpected."
                )
            return {"status": "error", "message": message}

        # Increment registered slots
        new_registered = registered + 1
        update_fields = {"registered": new_registered}

        # If capacity is reached, automatically close registration
        if new_registered >= capacity:
            update_fields["registration_open"] = False

        db_main.tournaments.update_one({"_id": oid}, {"$set": update_fields})

        # Fetch and return the updated tournament doc
        updated_tournament = db_main.tournaments.find_one({"_id": oid})
        # cafe_id never changes during registration - reuse the already-validated
        # `tournament` doc fetched above instead of re-checking updated_tournament for None.
        cafe_name = _resolve_cafe_name(db_main, tournament.get("cafe_id"))
        return {"status": "success", "tournament": map_tournament_doc(updated_tournament, cafe_name=cafe_name)}

    except Exception as e:
        print(f"[BookMyConsole] Failed to register for tournament: {e}")
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

        if game is not None:
            err = input_validation.validate_text(game, "Game", max_len=60)
            if err:
                return {"status": "error", "message": err}
            update_doc["game"] = game
        if title is not None:
            err = input_validation.validate_text(title, "Title", max_len=150)
            if err:
                return {"status": "error", "message": err}
            update_doc["title"] = title
        if prize is not None:
            err = input_validation.validate_text(prize, "Prize Pool", max_len=100)
            if err:
                return {"status": "error", "message": err}
            update_doc["prize"] = prize
        if entry is not None: update_doc["entry"] = entry
        if status is not None:
            update_doc["status"] = status
            if status == "cancelled":
                update_doc["registration_open"] = False

        if capacity is not None:
            capacity, err = input_validation.parse_bounded_number(capacity, "Capacity", min_val=1, max_val=100000)
            if err:
                return {"status": "error", "message": err}
            update_doc["capacity"] = capacity

        if entry == "Paid Entry" or (entry is None and existing.get("entry") == "Paid Entry"):
            if entry_fee is not None:
                entry_fee, err = input_validation.parse_bounded_number(entry_fee, "Entry Fee", min_val=0, max_val=1000000)
                if err:
                    return {"status": "error", "message": err}
                update_doc["entry_fee"] = entry_fee
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
        # cafe_id isn't an editable field here - reuse the already-validated `existing`
        # doc instead of re-checking updated_doc for None.
        cafe_name = _resolve_cafe_name(db_main, existing.get("cafe_id"))
        return {"status": "success", "tournament": map_tournament_doc(updated_doc, cafe_name=cafe_name)}

    except Exception as e:
        print(f"[BookMyConsole] Failed to update tournament: {e}")
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
        print(f"[BookMyConsole] Deleted tournament '{existing.get('title')}' and cleared {delete_regs.deleted_count} registrations.")

        return {"status": "success", "message": "Tournament and associated registrations deleted successfully."}

    except Exception as e:
        print(f"[BookMyConsole] Failed to delete tournament: {e}")
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
        print(f"[BookMyConsole] Failed to fetch tournament registrations: {e}")
        print(traceback.format_exc())
        return {"status": "error", "message": f"Failed to fetch registrations: {e}"}, 500
