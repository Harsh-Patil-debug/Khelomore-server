# favorites.py
# Handlers for managing user favorite cafes in a dedicated MongoDB collection

import pymongo
from .db_connection import db_main

_favorites_index_ensured = False


def _ensure_favorites_index():
    # Lazy + idempotent, mirroring payments.py's _ensure_used_payments_index - the
    # previous find-then-insert toggle was a check-then-act race: two near-simultaneous
    # toggle requests for the same cafe (a rapid double-tap firing two overlapping
    # requests before the first one's optimistic UI disabled the button) could both see
    # "not favorited yet" and both insert, leaving a duplicate row for the same
    # user+cafe. A unique index makes that structurally impossible instead of relying on
    # request timing.
    global _favorites_index_ensured
    if not _favorites_index_ensured:
        try:
            db_main.favorites.create_index([("user_email", 1), ("cafe_id", 1)], unique=True)
        except Exception:
            pass
        _favorites_index_ensured = True


def get_favorites_handler(user_email: str):
    """
    Fetches the list of favorite cafe IDs for the user from the dedicated 'favorites' collection.
    """
    if not user_email:
        return {"status": "error", "message": "User email is required"}, 400

    try:
        user_email = user_email.strip().lower()
        
        # Query the dedicated 'favorites' collection
        fav_docs = db_main.favorites.find({"user_email": user_email})
        favorites = [doc["cafe_id"] for doc in fav_docs if "cafe_id" in doc]

        return {
            "status": "success",
            "favorites": favorites
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve favorites: {str(e)}"
        }, 500


def toggle_favorite_handler(user_email: str, cafe_id: str):
    """
    Toggles a cafe ID in the dedicated 'favorites' collection for the user.
    """
    if not user_email:
        return {"status": "error", "message": "User email is required"}, 400
    if not cafe_id:
        return {"status": "error", "message": "Cafe ID is required"}, 400

    try:
        _ensure_favorites_index()
        user_email = user_email.strip().lower()
        cafe_id = cafe_id.strip()

        # Atomic delete-if-exists first: find_one_and_delete can't race with itself the
        # way a separate find_one + delete_one could. If nothing was there to delete,
        # fall through to inserting - the unique index (user_email, cafe_id) makes a
        # concurrent duplicate insert impossible, and a losing concurrent request is
        # treated as "already added" rather than an error.
        deleted = db_main.favorites.find_one_and_delete({
            "user_email": user_email,
            "cafe_id": cafe_id
        })

        if deleted:
            action = "removed from"
        else:
            try:
                db_main.favorites.insert_one({
                    "user_email": user_email,
                    "cafe_id": cafe_id
                })
            except pymongo.errors.DuplicateKeyError:
                pass  # a concurrent request already added it - same end state
            action = "added to"

        # Fetch the updated list of favorite cafe IDs
        fav_docs = db_main.favorites.find({"user_email": user_email})
        updated_favorites = [doc["cafe_id"] for doc in fav_docs if "cafe_id" in doc]

        return {
            "status": "success",
            "message": f"Cafe successfully {action} favorites",
            "favorites": updated_favorites
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to toggle favorite: {str(e)}"
        }, 500
