# game_images.py
# Global (platform-wide, not per-cafe) game name -> image mapping, managed exclusively
# from playhub-command by a super admin. Used by the customer mobile app's game-selection
# step so "Valorant"/"BGMI"/etc. show real artwork instead of a generic icon — matched
# against whatever free-text a cafe admin typed into a rig's installed_games (see
# rigs.py), never a foreign key, since no games catalog exists anywhere in this codebase
# by design (a cafe owner can still free-type any game name; it just won't have a custom
# image until a super admin adds one here).

import os
from datetime import datetime, timezone
import cloudinary
import cloudinary.uploader
from bson import ObjectId
from .db_connection import get_db
from .upload_validation import validate_image_upload
from . import input_validation

cloudinary_secret = os.getenv("CLOUDINARY_API_SECRET")
if not cloudinary_secret or cloudinary_secret == "your_api_secret_placeholder":
    print("[BookMyConsole Warning] CLOUDINARY_API_SECRET environment variable is not set.")
    cloudinary_secret = "your_api_secret_placeholder"

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "dghp9tq9m"),
    api_key=os.getenv("CLOUDINARY_API_KEY", "631388716584283"),
    api_secret=cloudinary_secret,
    secure=True
)

_index_ensured = False


def _ensure_index():
    global _index_ensured
    if not _index_ensured:
        try:
            db = get_db()
            if db is not None:
                # Case-insensitive-by-construction: name_normalized is always lowercase,
                # so this index also doubles as the "no two entries for the same game"
                # uniqueness guard (matching is normalized on read too — see the mobile
                # lookup this feeds).
                db.game_images.create_index("name_normalized", unique=True)
        except Exception:
            pass
        _index_ensured = True


def _normalize(name: str) -> str:
    return (name or "").strip().lower()


def _map_doc(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "imageUrl": doc.get("image_url", ""),
        # Defaults True — same reasoning as tournaments.featured: an entry created
        # before this field existed should still show on the home screen rather than
        # silently vanishing; a super admin hides a specific one explicitly instead.
        "featured": doc.get("featured", True),
        # Display order in "Games recommended for you" — super-admin controlled via
        # reorder_game_images_handler below, not alphabetical.
        "order": doc.get("order", 0),
        "createdAt": doc.get("created_at").isoformat() if isinstance(doc.get("created_at"), datetime) else None,
        "updatedAt": doc.get("updated_at").isoformat() if isinstance(doc.get("updated_at"), datetime) else None,
    }


def get_game_images_handler(featured_only=False):
    """Returns every game-image mapping — small, always-fetch-all-at-once list (no
    pagination needed; this is a curated set a super admin maintains by hand, not
    unbounded user content). Public read (no auth) — the mobile app's game picker needs
    this before a customer has necessarily logged in. featured_only=True scopes this to
    the home screen's "Games recommended for you" section."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "Database offline"}, 500
    db.game_images.update_many({"featured": {"$exists": False}}, {"$set": {"featured": True}})

    # One-time backfill: any doc missing `order` (created before reordering existed)
    # gets one assigned by its current name-sorted position, so the list doesn't jump
    # around arbitrarily before a super admin ever touches the new reorder controls.
    missing_order = list(db.game_images.find({"order": {"$exists": False}}).sort("name", 1))
    if missing_order:
        next_order = db.game_images.count_documents({"order": {"$exists": True}})
        for i, doc in enumerate(missing_order):
            db.game_images.update_one({"_id": doc["_id"]}, {"$set": {"order": next_order + i}})

    query = {"featured": True} if featured_only else {}
    docs = list(db.game_images.find(query).sort("order", 1))
    return {"status": "success", "gameImages": [_map_doc(d) for d in docs]}, 200


def reorder_game_images_handler(ordered_ids):
    """Super admin: persists a new display order for "Games recommended for you" — the
    full list of ids in their new order, one bulk write (order = index in the list)."""
    db = get_db()
    if db is None:
        return {"status": "error", "message": "Database offline"}, 500
    if not isinstance(ordered_ids, list) or not ordered_ids:
        return {"status": "error", "message": "ordered_ids must be a non-empty list."}, 400

    valid_ids = []
    for gid in ordered_ids:
        if not ObjectId.is_valid(gid):
            return {"status": "error", "message": f"Invalid id: {gid}"}, 400
        valid_ids.append(ObjectId(gid))

    found = db.game_images.count_documents({"_id": {"$in": valid_ids}})
    if found != len(valid_ids):
        return {"status": "error", "message": "One or more game images were not found."}, 404

    from pymongo import UpdateOne
    ops = [UpdateOne({"_id": oid}, {"$set": {"order": i}}) for i, oid in enumerate(valid_ids)]
    db.game_images.bulk_write(ops)
    return {"status": "success", "message": "Order updated."}, 200


def create_game_image_handler(data, files=None):
    db = get_db()
    if db is None:
        return {"status": "error", "message": "Database offline"}, 500
    _ensure_index()

    name = (data.get("name") or "").strip()
    name_error = input_validation.validate_text(name, "Game name", max_len=60)
    if name_error:
        return {"status": "error", "message": name_error}, 400

    image_file = files.get("image") if files else None
    if not image_file:
        return {"status": "error", "message": "An image file is required."}, 400
    validation_error = validate_image_upload(image_file)
    if validation_error:
        return {"status": "error", "message": validation_error}, 400

    name_normalized = _normalize(name)
    if db.game_images.find_one({"name_normalized": name_normalized}):
        return {"status": "error", "message": f"An image for '{name}' already exists. Edit that entry instead."}, 400

    try:
        upload_result = cloudinary.uploader.upload(image_file)
        image_url = upload_result.get("secure_url")
    except Exception as upload_err:
        print(f"[Cloudinary] Game image upload failed: {upload_err}")
        return {"status": "error", "message": "Image upload failed. Please try again."}, 502

    now = datetime.now(timezone.utc)
    doc = {
        "name": name,
        "name_normalized": name_normalized,
        "image_url": image_url,
        "featured": input_validation.parse_bool(data.get("featured"), default=True),
        # Appends to the end of "Games recommended for you" by default.
        "order": db.game_images.count_documents({}),
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = db.game_images.insert_one(doc)
    except Exception:
        # Race: two identical names inserted concurrently — the unique index is the real
        # guard, the find_one above is just a friendlier pre-check.
        return {"status": "error", "message": f"An image for '{name}' already exists. Edit that entry instead."}, 400
    doc["_id"] = result.inserted_id
    return {"status": "success", "gameImage": _map_doc(doc)}, 201


def update_game_image_handler(game_image_id, data, files=None):
    db = get_db()
    if db is None:
        return {"status": "error", "message": "Database offline"}, 500
    if not ObjectId.is_valid(game_image_id):
        return {"status": "error", "message": "Invalid id."}, 400
    existing = db.game_images.find_one({"_id": ObjectId(game_image_id)})
    if not existing:
        return {"status": "error", "message": "Not found."}, 404

    update_fields = {}
    if data.get("name") is not None:
        name = data.get("name").strip()
        name_error = input_validation.validate_text(name, "Game name", max_len=60)
        if name_error:
            return {"status": "error", "message": name_error}, 400
        name_normalized = _normalize(name)
        conflict = db.game_images.find_one({"name_normalized": name_normalized, "_id": {"$ne": ObjectId(game_image_id)}})
        if conflict:
            return {"status": "error", "message": f"An image for '{name}' already exists."}, 400
        update_fields["name"] = name
        update_fields["name_normalized"] = name_normalized

    image_file = files.get("image") if files else None
    if image_file:
        validation_error = validate_image_upload(image_file)
        if validation_error:
            return {"status": "error", "message": validation_error}, 400
        try:
            upload_result = cloudinary.uploader.upload(image_file)
            update_fields["image_url"] = upload_result.get("secure_url")
        except Exception as upload_err:
            print(f"[Cloudinary] Game image upload failed: {upload_err}")
            return {"status": "error", "message": "Image upload failed. Please try again."}, 502

    if data.get("featured") is not None:
        update_fields["featured"] = input_validation.parse_bool(data.get("featured"))

    if not update_fields:
        return {"status": "error", "message": "No fields to update."}, 400

    update_fields["updated_at"] = datetime.now(timezone.utc)
    db.game_images.update_one({"_id": ObjectId(game_image_id)}, {"$set": update_fields})
    updated = db.game_images.find_one({"_id": ObjectId(game_image_id)})
    return {"status": "success", "gameImage": _map_doc(updated)}, 200


def delete_game_image_handler(game_image_id):
    db = get_db()
    if db is None:
        return {"status": "error", "message": "Database offline"}, 500
    if not ObjectId.is_valid(game_image_id):
        return {"status": "error", "message": "Invalid id."}, 400
    result = db.game_images.delete_one({"_id": ObjectId(game_image_id)})
    if result.deleted_count == 0:
        return {"status": "error", "message": "Not found."}, 404
    return {"status": "success", "message": "Deleted."}, 200
