# partner_applications.py
# Handlers for managing partnership applications submitted by cafe owners

from datetime import datetime
from bson import ObjectId
from .db_connection import get_db
from .auth_handler import encrypt_phone_field, decrypt_phone_field

def get_partner_applications_handler():
    """
    Fetches all partner applications.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection not established."}, 500
    try:
        cursor = db_main.partner_applications.find({})
        apps = []
        for doc in cursor:
            apps.append({
                "id": str(doc["_id"]),
                "cafeName": doc.get("cafeName", ""),
                "ownerName": doc.get("ownerName", ""),
                "phone": decrypt_phone_field(doc.get("phone", "")),
                "email": doc.get("email", ""),
                "city": doc.get("city", ""),
                "state": doc.get("state", ""),
                "address": doc.get("address", ""),
                "pcCount": doc.get("pcCount", 0),
                "status": doc.get("status", "pending"),
                "submittedAt": doc.get("submittedAt") or doc.get("submitted_at") or datetime.now().isoformat(),
            })
        return {"status": "success", "applications": apps}, 200
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve applications: {e}"}, 500

def create_partner_application_handler(data):
    """
    Creates a new partner application.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection not established."}, 500
    try:
        cafe_name = data.get("cafeName")
        owner_name = data.get("ownerName")
        phone = data.get("phone")
        email = data.get("email")
        city = data.get("city")
        state = data.get("state")
        address = data.get("address")
        pc_count = data.get("pcCount")

        if not cafe_name or not owner_name or not phone or not email or not city or not state or not address or not pc_count:
            return {"status": "error", "message": "All fields are required."}, 400

        doc = {
            "cafeName": cafe_name,
            "ownerName": owner_name,
            "phone": encrypt_phone_field(phone),
            "email": email,
            "city": city,
            "state": state,
            "address": address,
            "pcCount": pc_count,
            "status": "pending",
            "submittedAt": datetime.now().isoformat()
        }

        res = db_main.partner_applications.insert_one(doc)
        doc["id"] = str(res.inserted_id)
        # Decrypt phone for response
        doc["phone"] = phone
        doc.pop("_id", None)
        return {"status": "success", "application": doc}, 201
    except Exception as e:
        return {"status": "error", "message": f"Failed to submit application: {e}"}, 500

def update_partner_application_status_handler(app_id, status_val):
    """
    Updates the status of a partner application.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection not established."}, 500
    try:
        oid = ObjectId(app_id)
    except Exception:
        return {"status": "error", "message": "Invalid application ID format."}, 400
    try:
        res = db_main.partner_applications.update_one({"_id": oid}, {"$set": {"status": status_val}})
        if res.matched_count == 0:
            return {"status": "error", "message": "Application not found."}, 404
        return {"status": "success", "message": f"Application status updated to {status_val}."}, 200
    except Exception as e:
        return {"status": "error", "message": f"Failed to update application: {e}"}, 500
