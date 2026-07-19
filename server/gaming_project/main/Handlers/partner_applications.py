# partner_applications.py
# Handlers for managing partnership applications submitted by cafe owners

from datetime import datetime
import cloudinary
import cloudinary.uploader
from bson import ObjectId
from .db_connection import get_db
from .auth_handler import encrypt_phone_field, decrypt_phone_field
from .upload_validation import validate_image_upload
from . import input_validation

# Optional fields: stored if provided, never required.
_OPTIONAL_FIELDS = [
    "ps5Count", "otherDevices", "openingHours", "website", "instagram", "mapsLink", "message",
    # Shared with the super admin's "Add Gaming Cafe" form.
    "latitude", "longitude", "specs", "imageUrl",
]

# Public, unauthenticated form — cap how many photos a single submission can attach. Also
# matches the gamer app's cafe-detail slider, which only ever shows up to 3 photos.
_MAX_PHOTOS = 3


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
            app = {
                "id": str(doc["_id"]),
                "cafeName": doc.get("cafeName", ""),
                "ownerName": doc.get("ownerName", ""),
                "phone": decrypt_phone_field(doc.get("phone", "")),
                "email": doc.get("email", ""),
                "city": doc.get("city", ""),
                "state": doc.get("state", ""),
                "address": doc.get("address", ""),
                "pcCount": doc.get("pcCount", 0),
                "area": doc.get("area", ""),
                "pricePerHour": doc.get("pricePerHour"),
                "rating": doc.get("rating"),
                "reviews": doc.get("reviews"),
                "status": doc.get("status", "pending"),
                "submittedAt": doc.get("submittedAt") or doc.get("submitted_at") or datetime.now().isoformat(),
                "photos": doc.get("photos", []),
                "logo": doc.get("logo", ""),
            }
            for field in _OPTIONAL_FIELDS:
                app[field] = doc.get(field, "")
            apps.append(app)
        return {"status": "success", "applications": apps}, 200
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve applications: {e}"}, 500


def create_partner_application_handler(data, files=None):
    """
    Creates a new partner application. `files` (optional) may contain a multi-file
    "photos" upload and a single "logo" upload — both go to Cloudinary, same pattern as
    cafes.py/tournaments.py. A failed/invalid image upload does not fail the whole
    application; it's just omitted, since the photos/logo are supplementary, not required.
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
        area = data.get("area")
        price_per_hour = data.get("pricePerHour")

        # area/pricePerHour mirror the same two fields the super admin's "Add Gaming Cafe"
        # form requires, so the two forms collect the same required set end to end.
        if not cafe_name or not owner_name or not phone or not email or not city or not state or not address or not pc_count or not area or price_per_hour is None:
            return {"status": "error", "message": "All fields are required."}, 400

        # SECURITY: this is the only fully public, unauthenticated form-submission endpoint
        # in the whole platform — every field below only ever had a client-side check
        # (maxLength/type=email/type=url/min/max), which a direct POST bypasses entirely.
        validation_error = (
            input_validation.validate_text(cafe_name, "Cafe name", max_len=100)
            or input_validation.validate_text(owner_name, "Owner name", max_len=80)
            or input_validation.validate_phone(phone)
            or input_validation.validate_email(email)
            or input_validation.validate_text(city, "City", max_len=60)
            or input_validation.validate_text(state, "State", max_len=60)
            or input_validation.validate_text(address, "Address", max_len=400)
            or input_validation.validate_text(area, "Area", max_len=100)
        )
        if validation_error:
            return {"status": "error", "message": validation_error}, 400

        pc_count, pc_count_error = input_validation.parse_bounded_number(pc_count, "PC count", min_val=1, max_val=999)
        if pc_count_error:
            return {"status": "error", "message": pc_count_error}, 400

        price_per_hour, price_error = input_validation.parse_bounded_number(
            price_per_hour, "Price Per Hour", min_val=0, max_val=100000
        )
        if price_error:
            return {"status": "error", "message": price_error}, 400

        ps5_count = data.get("ps5Count")
        if ps5_count:
            _, err = input_validation.parse_bounded_number(ps5_count, "PS5 count", min_val=0, max_val=999)
            if err:
                return {"status": "error", "message": err}, 400

        # Same optional fields the "Add Gaming Cafe" admin form collects — kept optional
        # here too, since a rating/review count for a not-yet-listed cafe is inherently
        # a placeholder, not something an applicant should be forced to fabricate.
        rating_val = None
        if data.get("rating"):
            rating_val, err = input_validation.parse_bounded_number(data.get("rating"), "Rating", min_val=0, max_val=5, is_float=True)
            if err:
                return {"status": "error", "message": err}, 400

        reviews_val = None
        if data.get("reviews"):
            reviews_val, err = input_validation.parse_bounded_number(data.get("reviews"), "Reviews", min_val=0)
            if err:
                return {"status": "error", "message": err}, 400

        for coord_field, label, bound in (("latitude", "Latitude", 90), ("longitude", "Longitude", 180)):
            value = data.get(coord_field)
            if value:
                _, err = input_validation.parse_bounded_number(value, label, min_val=-bound, max_val=bound, is_float=True)
                if err:
                    return {"status": "error", "message": err}, 400

        for field in ("website", "mapsLink", "imageUrl"):
            value = data.get(field)
            if value:
                err = input_validation.validate_url(value, field)
                if err:
                    return {"status": "error", "message": err}, 400

        optional_text_limits = {"otherDevices": 200, "openingHours": 80, "instagram": 80, "message": 800, "specs": 200}
        for field, max_len in optional_text_limits.items():
            value = data.get(field)
            if value:
                err = input_validation.validate_text(value, field, max_len=max_len, required=False)
                if err:
                    return {"status": "error", "message": err}, 400

        doc = {
            "cafeName": cafe_name,
            "ownerName": owner_name,
            "phone": encrypt_phone_field(phone),
            "email": email,
            "city": city,
            "state": state,
            "address": address,
            "pcCount": pc_count,
            "area": area,
            "pricePerHour": price_per_hour,
            "status": "pending",
            "submittedAt": datetime.now().isoformat()
        }
        if rating_val is not None:
            doc["rating"] = rating_val
        if reviews_val is not None:
            doc["reviews"] = reviews_val
        for field in _OPTIONAL_FIELDS:
            value = data.get(field)
            if value:
                doc[field] = value

        photo_urls = []
        logo_url = ""
        if files:
            photo_files = files.getlist("photos") if hasattr(files, "getlist") else ([files["photos"]] if "photos" in files else [])
            photo_files = photo_files[:_MAX_PHOTOS]
            for photo_file in photo_files:
                validation_error = validate_image_upload(photo_file)
                if validation_error:
                    continue  # skip invalid files silently rather than fail the whole application
                try:
                    result = cloudinary.uploader.upload(photo_file)
                    url = result.get("secure_url")
                    if url:
                        photo_urls.append(url)
                except Exception as upload_err:
                    print(f"[Cloudinary] Partner application photo upload failed: {upload_err}")

            if "logo" in files:
                logo_file = files["logo"]
                validation_error = validate_image_upload(logo_file)
                if not validation_error:
                    try:
                        result = cloudinary.uploader.upload(logo_file)
                        logo_url = result.get("secure_url") or ""
                    except Exception as upload_err:
                        print(f"[Cloudinary] Partner application logo upload failed: {upload_err}")

        if photo_urls:
            doc["photos"] = photo_urls
        if logo_url:
            doc["logo"] = logo_url

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
    enum_error = input_validation.validate_enum(status_val, {"pending", "approved", "rejected"}, "Status")
    if enum_error:
        return {"status": "error", "message": enum_error}, 400
    try:
        res = db_main.partner_applications.update_one({"_id": oid}, {"$set": {"status": status_val}})
        if res.matched_count == 0:
            return {"status": "error", "message": "Application not found."}, 404
        return {"status": "success", "message": f"Application status updated to {status_val}."}, 200
    except Exception as e:
        return {"status": "error", "message": f"Failed to update application: {e}"}, 500
