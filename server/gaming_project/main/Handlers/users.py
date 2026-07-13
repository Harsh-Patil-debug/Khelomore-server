# users.py
# Handlers for managing application users (gamers) from the super admin panel

from datetime import datetime
from bson import ObjectId
from .db_connection import db_main

def get_users_handler():
    """
    Fetches the list of all application users (gamers).
    Returns (data, status_code).
    """
    try:
        users_cursor = db_main.users.find({})
        users_list = []
        for doc in users_cursor:
            # Map doc safely
            user_id = str(doc.get("_id"))
            gamertag = doc.get("gamertag") or doc.get("first_name") or "GAMER"
            email = doc.get("email", "")
            phone_raw = doc.get("phone") or doc.get("phoneNumber") or doc.get("contact_phone") or ""
            from .auth_handler import decrypt_phone_field
            phone = decrypt_phone_field(phone_raw)
            wallet_balance = float(doc.get("wallet_balance") or doc.get("wallet") or 0.0)
            
            # Map status
            status_val = doc.get("status", "Active")
            # If status isn't "Suspended" but user is marked suspended in doc, map accordingly
            suspended = status_val == "Suspended" or doc.get("suspended", False)
            
            created_at = doc.get("createdAt") or doc.get("created_at")
            if isinstance(created_at, datetime):
                created_at_str = created_at.isoformat()
            else:
                created_at_str = str(created_at) if created_at else datetime.now().isoformat()
                
            users_list.append({
                "id": user_id,
                "full_name": gamertag,
                "email": email,
                "phone": phone,
                "wallet_balance": wallet_balance,
                "created_at": created_at_str,
                "suspended": suspended,
                "status": "Suspended" if suspended else status_val
            })
            
        return {
            "status": "success",
            "users": users_list
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve users: {str(e)}"
        }, 500

def toggle_user_status_handler(user_id: str):
    """
    Toggles a user's suspended status.
    """
    if not user_id:
        return {"status": "error", "message": "User ID is required"}, 400
        
    try:
        oid = ObjectId(user_id)
    except Exception:
        return {"status": "error", "message": "Invalid User ID format"}, 400
        
    try:
        user = db_main.users.find_one({"_id": oid})
        if not user:
            return {"status": "error", "message": "User not found"}, 404
            
        current_status = user.get("status", "Active")
        new_suspended = not (current_status == "Suspended" or user.get("suspended", False))
        new_status = "Suspended" if new_suspended else "Active"
        
        db_main.users.update_one(
            {"_id": oid},
            {"$set": {
                "status": new_status,
                "suspended": new_suspended
            }}
        )
        
        return {
            "status": "success",
            "message": f"User status successfully updated to {new_status}.",
            "suspended": new_suspended,
            "user_status": new_status
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to update user status: {str(e)}"
        }, 500
