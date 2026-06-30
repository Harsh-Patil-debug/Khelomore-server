# favorites.py
# Handlers for managing user favorite cafes in a dedicated MongoDB collection

from .db_connection import db_main

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
        user_email = user_email.strip().lower()
        cafe_id = cafe_id.strip()

        # Check if the record already exists in the dedicated 'favorites' collection
        existing = db_main.favorites.find_one({
            "user_email": user_email,
            "cafe_id": cafe_id
        })

        if existing:
            # Remove the favorite document
            db_main.favorites.delete_one({"_id": existing["_id"]})
            action = "removed"
        else:
            # Insert a new favorite document
            db_main.favorites.insert_one({
                "user_email": user_email,
                "cafe_id": cafe_id
            })
            action = "added"

        # Fetch the updated list of favorite cafe IDs
        fav_docs = db_main.favorites.find({"user_email": user_email})
        updated_favorites = [doc["cafe_id"] for doc in fav_docs if "cafe_id" in doc]

        return {
            "status": "success",
            "message": f"Cafe successfully {action} to favorites",
            "favorites": updated_favorites
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to toggle favorite: {str(e)}"
        }, 500
