# cafes.py
# Handlers for managing gaming cafes, including real-time distance calculations

import os
import json
import math
import random
import cloudinary
import cloudinary.uploader
from bson import ObjectId
from .db_connection import get_db
from .upload_validation import validate_image_upload
from . import input_validation

# Default standard operating slots (10:00 AM - 10:00 PM, 1-hour intervals)
DEFAULT_SLOTS = [
    "10:00 AM - 11:00 AM",
    "11:00 AM - 12:00 PM",
    "12:00 PM - 01:00 PM",
    "01:00 PM - 02:00 PM",
    "02:00 PM - 03:00 PM",
    "03:00 PM - 04:00 PM",
    "04:00 PM - 05:00 PM",
    "05:00 PM - 06:00 PM",
    "06:00 PM - 07:00 PM",
    "07:00 PM - 08:00 PM",
    "08:00 PM - 09:00 PM",
    "09:00 PM - 10:00 PM",
    "10:00 PM - 11:00 PM",
    "11:00 PM - 12:00 AM",
]


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

# Seed cafes mapped with exact latitude/longitude coordinates around Nerul and Bandra/BKC/Andheri
SEED_CAFES = [
    {
        "name": "Red Zone Gaming Cafe",
        "distance_km": 0.3,
        "latitude": 19.0418,
        "longitude": 73.0208,
        "rating": 4.8,
        "reviews": 320,
        "area": "Sector 3, Nerul",
        "specs": ["RTX 4090", "240Hz+", "VIP Lounge"],
        "price_per_hour": 170,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075374/t9y65e3kk7iwalkaw2gr.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075375/jivkorclim8av3didmvb.jpg"
        ]
    },
    {
        "name": "Gear Up Gaming Nerul",
        "distance_km": 1.0,
        "latitude": 19.0330,
        "longitude": 73.0155,
        "rating": 4.9,
        "reviews": 748,
        "area": "Sector 15, Nerul",
        "specs": ["RTX 4090", "360Hz", "VIP Lounge", "Sim Rigs"],
        "price_per_hour": 200,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782199128/gear_up_img1.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075377/q5rjmhxf04mtye2ogler.jpg"
        ]
    },
    {
        "name": "Vortex Lounge Nerul",
        "distance_km": 1.5,
        "latitude": 19.0480,
        "longitude": 73.0245,
        "rating": 4.7,
        "reviews": 512,
        "area": "Sector 21, Nerul",
        "specs": ["RTX 4080", "240Hz", "PS5 Pro", "VR Booth"],
        "price_per_hour": 160,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075378/h8vobzu6tsac90wd4wn2.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075378/xgdl5iysbxyoqb4npofp.jpg"
        ]
    },
    {
        "name": "Pro Gamers Cafe",
        "distance_km": 0.7,
        "latitude": 19.0375,
        "longitude": 73.0182,
        "rating": 4.6,
        "reviews": 218,
        "area": "Sector 4, Nerul",
        "specs": ["RTX 4080", "165Hz", "Console Lounge"],
        "price_per_hour": 140,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075380/koyu2b50jdwkaiidjbii.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075380/wjpat4di0ksm6ccnpmkd.jpg"
        ]
    },
    {
        "name": "Neon Arena Bandra",
        "distance_km": 18.2,
        "latitude": 19.0596,
        "longitude": 72.8295,
        "rating": 4.8,
        "reviews": 1284,
        "area": "Bandra West",
        "specs": ["RTX 4090", "240Hz", "VIP Lounge"],
        "price_per_hour": 180,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782199129/neon_arena_img1.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075382/zi8usuenoomgytyzr8xi.jpg"
        ]
    },
    {
        "name": "Pixel Bunker Andheri",
        "distance_km": 24.6,
        "latitude": 19.1154,
        "longitude": 72.8727,
        "rating": 4.7,
        "reviews": 942,
        "area": "Andheri East",
        "specs": ["RTX 4080", "165Hz", "PS5 Pro"],
        "price_per_hour": 150,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075384/pbvrsyzsk14fy4fldynx.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075384/ogc1mk2sopjsjnyoueze.jpg"
        ]
    },
    {
        "name": "Rogue Circuit Powai",
        "distance_km": 20.1,
        "latitude": 19.1176,
        "longitude": 72.9060,
        "rating": 4.9,
        "reviews": 2103,
        "area": "Powai",
        "specs": ["RTX 4090", "360Hz", "Sim Rigs", "Console Lounge"],
        "price_per_hour": 220,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782199130/rogue_circuit_img1.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782199131/rogue_circuit_img2.jpg"
        ]
    },
    {
        "name": "Ghost Protocol BKC",
        "distance_km": 16.8,
        "latitude": 19.0607,
        "longitude": 72.8633,
        "rating": 4.6,
        "reviews": 612,
        "area": "BKC",
        "specs": ["RTX 4070 Ti", "240Hz", "VR Booth"],
        "price_per_hour": 160,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075387/qr9qrswu2rs2hzlg26bw.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782199132/ghost_protocol_img2.jpg"
        ]
    }
]


def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    """Calculates the great-circle distance between two points on Earth in kilometers."""
    try:
        # Convert decimal degrees to radians
        lat1, lon1, lat2, lon2 = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])

        # Haversine formula
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        r = 6371.0  # Radius of Earth in kilometers
        return round(c * r, 1)
    except Exception:
        return None


def generate_slots_from_hours(operating_hours):
    """
    Generates 1-hour interval slots from open time to close time.
    operating_hours: dict with keys 'open' (e.g. '10:00') and 'close' (e.g. '23:00').
    Returns a list of slot strings.
    """
    if not operating_hours or "open" not in operating_hours or "close" not in operating_hours:
        return DEFAULT_SLOTS
        
    try:
        from datetime import datetime, timedelta
        open_str = operating_hours["open"]
        close_str = operating_hours["close"]
        
        # Parse times
        start_time = datetime.strptime(open_str, "%H:%M")
        end_time = datetime.strptime(close_str, "%H:%M")
        
        # Handle cases where close time is on the next day
        if end_time <= start_time:
            end_time += timedelta(days=1)
            
        slots = []
        current = start_time
        while current < end_time:
            next_hour = current + timedelta(hours=1)
            slot_start = current.strftime("%I:%M %p").lstrip("0")
            slot_end = next_hour.strftime("%I:%M %p").lstrip("0")
            slots.append(f"{slot_start} - {slot_end}")
            current = next_hour
            
        return slots if slots else DEFAULT_SLOTS
    except Exception as e:
        print(f"[KheloMore Error] Failed to generate slots: {e}")
        return DEFAULT_SLOTS


def _effective_cafe_status(doc):
    """
    A cafe's displayed status isn't just the manually-toggled `status` field — a cafe more
    than SUBSCRIPTION_GRACE_DAYS past its subscription due date must show as "suspended"
    automatically, the moment anyone next looks at it, without needing a scheduled job or
    the super admin to have manually flipped the toggle. Computed on every read (same
    lazy/on-read convention subscriptions.py itself uses for subscription_status), not
    written back here — the write only happens via subscriptions._ensure_defaults, which
    this intentionally does not duplicate.

    "pending"/"rejected" are pre-onboarding states unrelated to billing and are never
    overridden by a subscription check.
    """
    stored_status = doc.get("status", "active")
    if stored_status in ("pending", "rejected"):
        return stored_status

    grace_until = doc.get("subscription_grace_until")
    if grace_until is not None:
        from datetime import datetime, timezone
        if grace_until.tzinfo is None:
            grace_until = grace_until.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > grace_until:
            return "suspended"

    return stored_status


def map_cafe_doc(doc, user_lat=None, user_lon=None, public=False):
    """
    Maps a MongoDB cafe document to the format expected by the React Native frontend.

    public=True (used for the unauthenticated cafe listing/detail endpoints) omits
    owner_email — unlike phone/address (normal, expected business contact info for a
    directory app), owner_email doubles as the cafe admin's login identifier, so exposing
    it publicly hands out a ready-made target list for phishing/credential attacks against
    specific cafe admin accounts. contact_email must be an explicitly-set value on the
    public path — it no longer silently falls back to owner_email.
    """

    calculated_distance = None
    if user_lat is not None and user_lon is not None:
        cafe_lat = doc.get("latitude")
        cafe_lon = doc.get("longitude")
        if cafe_lat is not None and cafe_lon is not None:
            calculated_distance = calculate_haversine_distance(user_lat, user_lon, cafe_lat, cafe_lon)

    # Fallback to the static distance stored in the database if user coordinates are not sent
    distance_km = calculated_distance if calculated_distance is not None else float(doc.get("distance_km", 0.0))

    # Extract helper fields for image/amenities fallback
    images_list = doc.get("images", [])
    specs_list = doc.get("specs", [])

    owner_email = doc.get("owner_email", "")
    explicit_contact_email = doc.get("contact_email", "")
    if public:
        response_owner_email = ""
        response_contact_email = explicit_contact_email
    else:
        response_owner_email = owner_email
        response_contact_email = explicit_contact_email or owner_email

    return {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "distanceKm": distance_km,
        "latitude": float(doc.get("latitude")) if doc.get("latitude") is not None else None,
        "longitude": float(doc.get("longitude")) if doc.get("longitude") is not None else None,
        "rating": float(doc.get("rating", 0.0)),
        "reviews": int(doc.get("reviews", 0)),
        "area": doc.get("area", ""),
        "specs": specs_list,
        "pricePerHour": int(doc.get("price_per_hour", 0)),
        "price_per_hour": int(doc.get("price_per_hour", 0)),
        "images": images_list,
        "slots": doc.get("slots") or generate_slots_from_hours(doc.get("operating_hours")),
        "owner_email": response_owner_email,
        "contact_email": response_contact_email,
        "address": doc.get("address", ""),
        "city": doc.get("city", ""),
        "phone": doc.get("phone", ""),
        "is_deleted": doc.get("is_deleted", False),
        "status": _effective_cafe_status(doc),

        # Profile specific fields
        "banner_url": doc.get("banner_url") or (images_list[0] if images_list else ""),
        "logo_url": doc.get("logo_url", ""),
        "operating_hours": doc.get("operating_hours") or {"open": "10:00", "close": "23:00"},
        "amenities": doc.get("amenities") or specs_list,
        "social": doc.get("social") or {"instagram": "", "youtube": "", "facebook": ""},

        # Fields shared with the public partner-application form.
        "ownerName": doc.get("owner_name", ""),
        "state": doc.get("state", ""),
        "pcCount": doc.get("pc_count"),
        "ps5Count": doc.get("ps5_count"),
        "otherDevices": doc.get("other_devices", ""),
        "openingHours": doc.get("opening_hours_text", ""),
        "website": doc.get("website", ""),
        "message": doc.get("message", ""),

        # Subscription (15-day free trial, then ₹1599/month) — callers that need this
        # fresh should call subscriptions._ensure_defaults(db, doc) before mapping; these
        # are safe fallbacks for callers that don't (e.g. the public listing, which never
        # shows this to a cafe owner anyway).
        "subscription_plan": "Pro",
        "subscription_amount": doc.get("subscription_amount", 1599),
        "subscription_status": doc.get("subscription_status", "active"),
        "subscription_renewal": doc["subscription_due_date"].isoformat() if doc.get("subscription_due_date") else None,
        "subscription_trial_welcome_shown": bool(doc.get("subscription_trial_welcome_shown")),

        # Whether the owner has connected their own Razorpay account for booking
        # payments. Never expose the actual key_id/secret here — see
        # get_razorpay_credentials_status_handler for that (owner/super-admin only).
        "razorpay_configured": bool(doc.get("razorpay_key_id") and doc.get("razorpay_key_secret_enc")),
    }


def get_cafes_handler(latitude=None, longitude=None, include_deleted=False):
    """Retrieves all gaming cafes from the database. Seeds the DB if empty."""
    db_main = get_db()
    if db_main is None:
        return {
            "status": "error",
            "message": "MongoDB connection is not established."
        }

    try:
        # Auto-seeding disabled to prevent mock cafes from populating
        # if db_main.cafes.count_documents({}) == 0:
        #     db_main.cafes.insert_many(SEED_CAFES)
        #     print("[KheloMore] Database seeded with default gaming cafes.")
        pass

        # Parse request coordinates if present
        user_lat = None
        user_lon = None
        if latitude is not None and longitude is not None:
            lat_str = str(latitude).strip().lower()
            lon_str = str(longitude).strip().lower()
            if lat_str and lat_str != "null" and lat_str != "undefined" and lon_str and lon_str != "null" and lon_str != "undefined":
                try:
                    user_lat = float(latitude)
                    user_lon = float(longitude)
                except ValueError:
                    pass

        # Retrieve all cafes and sort by calculated distance (nearest first)
        query: dict = {} if include_deleted else {"is_deleted": {"$ne": True}}
        if not include_deleted:
            # A cafe more than the grace period past its ₹1500/month subscription due
            # date is hidden from the public listing until paid — the cafe owner's own
            # dashboard is unaffected (it goes through get_my_cafes_handler, not this).
            # Cafes that predate this feature (no subscription_grace_until set yet) are
            # NOT retroactively hidden — that field only exists once something has
            # actually looked at that cafe's subscription (see subscriptions.py).
            from datetime import datetime, timezone, timedelta
            now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
            query["$or"] = [
                {"subscription_grace_until": {"$exists": False}},
                {"subscription_grace_until": {"$gte": now_ist}},
            ]
            # A super admin's manual "Suspend Hub" (status field) is a separate reason to
            # hide a cafe from the public listing, independent of billing.
            query["status"] = {"$ne": "suspended"}
        docs = list(db_main.cafes.find(query))
        # include_deleted is already super-admin-gated at the view layer, so that path may
        # see full owner detail; the plain public listing must not.
        mapped_cafes = [map_cafe_doc(d, user_lat, user_lon, public=not include_deleted) for d in docs]
        mapped_cafes.sort(key=lambda c: c["distanceKm"])
        
        return {
            "status": "success",
            "cafes": mapped_cafes
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve cafes: {e}"
        }


def create_cafe_handler(data, files=None):
    """Validates and creates a new gaming cafe in the database, with automatic Cloudinary image upload."""
    db_main = get_db()
    if db_main is None:
        return {
            "status": "error",
            "message": "MongoDB connection is not established."
        }

    try:
        name = data.get("name")
        area = data.get("area")
        price_per_hour = data.get("pricePerHour") or data.get("price_per_hour")
        distance_km = data.get("distanceKm")
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        specs = data.get("specs", [])
        images = data.get("images") or data.get("imageUrl") or data.get("image_url") or []

        # Basic validations
        if not name or not area or price_per_hour is None:
            return {
                "status": "error",
                "message": "Name, Area, and Price Per Hour are required fields."
            }

        validation_error = (
            input_validation.validate_text(name, "Name", max_len=100)
            or input_validation.validate_text(area, "Area", max_len=100)
        )
        if validation_error:
            return {"status": "error", "message": validation_error}

        price_per_hour, price_error = input_validation.parse_bounded_number(
            price_per_hour, "Price Per Hour", min_val=0, max_val=100000
        )
        if price_error:
            return {"status": "error", "message": price_error}

        owner_email_input = data.get("owner_email") or data.get("ownerEmail") or data.get("contact_email") or data.get("contactEmail")
        if owner_email_input:
            email_error = input_validation.validate_email(owner_email_input)
            if email_error:
                return {"status": "error", "message": email_error}

        phone_input = data.get("phone")
        if phone_input:
            phone_error = input_validation.validate_phone(phone_input, required=False)
            if phone_error:
                return {"status": "error", "message": phone_error}

        # Fields shared with the public partner-application form — kept optional here since
        # a cafe can also be created directly by a super admin without going through that
        # flow at all, but validated the same way whenever they're present.
        owner_name_input = data.get("ownerName") or data.get("owner_name")
        if owner_name_input:
            err = input_validation.validate_text(owner_name_input, "Owner name", max_len=80, required=False)
            if err:
                return {"status": "error", "message": err}

        state_input = data.get("state")
        if state_input:
            err = input_validation.validate_text(state_input, "State", max_len=60, required=False)
            if err:
                return {"status": "error", "message": err}

        pc_count_val, pc_count_err = input_validation.parse_bounded_number(
            data.get("pcCount"), "PC count", min_val=0, max_val=999, required=False
        )
        if pc_count_err:
            return {"status": "error", "message": pc_count_err}

        ps5_count_val, ps5_count_err = input_validation.parse_bounded_number(
            data.get("ps5Count"), "PS5 count", min_val=0, max_val=999, required=False
        )
        if ps5_count_err:
            return {"status": "error", "message": ps5_count_err}

        for field_key, label, max_len in (
            ("otherDevices", "Other devices", 200),
            ("openingHours", "Opening hours", 80),
            ("instagram", "Instagram", 80),
            ("message", "Message", 800),
        ):
            value = data.get(field_key)
            if value:
                err = input_validation.validate_text(value, label, max_len=max_len, required=False)
                if err:
                    return {"status": "error", "message": err}

        website_input = data.get("website")
        if website_input:
            err = input_validation.validate_url(website_input, "Website")
            if err:
                return {"status": "error", "message": err}

        # Parse coordinates — out-of-range/invalid values fall back to the Nerul default
        # below rather than hard-failing, matching this handler's existing lenient style
        # for a field that's genuinely optional at signup time.
        try:
            latitude = float(latitude) if latitude is not None else None
            if latitude is not None and not (-90 <= latitude <= 90):
                latitude = None
        except ValueError:
            latitude = None

        try:
            longitude = float(longitude) if longitude is not None else None
            if longitude is not None and not (-180 <= longitude <= 180):
                longitude = None
        except ValueError:
            longitude = None

        # Default coordinates to Nerul centre with small random offset if not provided
        if latitude is None or longitude is None:
            latitude = 19.0330 + random.uniform(-0.015, 0.015)
            longitude = 73.0190 + random.uniform(-0.015, 0.015)

        # Parse distance
        try:
            distance_km = float(distance_km) if distance_km is not None else None
        except ValueError:
            distance_km = None

        # Fallback static distance relative to Nerul centre (19.0330, 73.0190)
        if distance_km is None:
            calc_dist = calculate_haversine_distance(19.0330, 73.0190, latitude, longitude)
            distance_km = calc_dist if calc_dist is not None else 1.0

        # Handle specs parsing (FormData strings)
        if isinstance(specs, str):
            try:
                specs = json.loads(specs)
            except Exception:
                specs = [s.strip() for s in specs.split(",") if s.strip()]

        # Handle Cloudinary upload (supports multiple files: image, image_1, image_2, ...)
        uploaded_urls = []
        logo_url = ""
        if files:
            upload_keys = ["image"] + [f"image_{i}" for i in range(1, 20)]
            for key in upload_keys:
                if key in files:
                    image_file = files[key]
                    validation_error = validate_image_upload(image_file)
                    if validation_error:
                        return {"status": "error", "message": f"'{key}': {validation_error}"}
                    try:
                        upload_result = cloudinary.uploader.upload(image_file)
                        url = upload_result.get("secure_url")
                        if url:
                            uploaded_urls.append(url)
                            print(f"[Cloudinary] Uploaded '{key}' -> {url}")
                    except Exception as upload_err:
                        print(f"[Cloudinary] Upload failed for '{key}': {upload_err}")

            # Separate single logo upload — same field name/behavior as the partner
            # application form, so a logo attached there can carry straight through.
            if "logo" in files:
                logo_file = files["logo"]
                logo_validation_error = validate_image_upload(logo_file)
                if not logo_validation_error:
                    try:
                        logo_upload_result = cloudinary.uploader.upload(logo_file)
                        logo_url = logo_upload_result.get("secure_url") or ""
                    except Exception as upload_err:
                        print(f"[Cloudinary] Logo upload failed: {upload_err}")

        # A logo URL may also arrive as a plain string (e.g. carried over from an approved
        # partner application that already uploaded one), not just as a fresh file upload.
        if not logo_url:
            logo_url_input = data.get("logoUrl") or data.get("logo_url") or data.get("logo")
            if logo_url_input and isinstance(logo_url_input, str) and not input_validation.validate_url(logo_url_input, "Logo URL"):
                logo_url = logo_url_input

        # Construct final images list
        final_images = list(uploaded_urls)

        # Parse and combine text URLs
        text_images = []
        if isinstance(images, list) and len(images) > 0:
            text_images = images
        elif isinstance(images, str) and images.strip():
            try:
                parsed_images = json.loads(images)
                if isinstance(parsed_images, list):
                    text_images = parsed_images
                else:
                    text_images = [str(parsed_images)]
            except Exception:
                if "," in images:
                    text_images = [img.strip() for img in images.split(",") if img.strip()]
                else:
                    text_images = [images.strip()]

        # Text-supplied image URLs (unlike Cloudinary uploads, these are arbitrary
        # client-supplied strings) — drop anything that isn't a real http(s) URL rather
        # than storing it as-is and later rendering it as an <img src>.
        text_images = [img for img in text_images if isinstance(img, str) and not input_validation.validate_url(img, "Image URL")]

        final_images.extend(text_images)

        if not final_images:
            final_images = ["https://images.unsplash.com/photo-1542751371-adc38448a05e?q=80&w=600"]

        # Construct MongoDB document. Rating/reviews are cosmetic display fields with a
        # sensible fallback already established by this handler — clamp out-of-range values
        # to that same fallback rather than hard-failing cafe creation over them.
        try:
            rating_val = float(data.get("rating") or 5.0)
            if not (0 <= rating_val <= 5):
                rating_val = 5.0
        except (ValueError, TypeError):
            rating_val = 5.0

        try:
            reviews_val = int(data.get("reviews") or data.get("review_count") or 0)
            if reviews_val < 0:
                reviews_val = 0
        except (ValueError, TypeError):
            reviews_val = 0

        cafe_doc = {
            "name": name,
            "area": area,
            "price_per_hour": price_per_hour,
            "distance_km": distance_km,
            "latitude": latitude,
            "longitude": longitude,
            "rating": rating_val,
            "reviews": reviews_val,
            "specs": specs,
            "images": final_images,
            "logo_url": logo_url,
            "owner_email": data.get("owner_email") or data.get("ownerEmail") or data.get("contact_email") or data.get("contactEmail") or "",
            "address": data.get("address") or "",
            "city": data.get("city") or "",
            "phone": data.get("phone") or "",
            # Fields shared with the public partner-application form.
            "owner_name": owner_name_input or "",
            "state": state_input or "",
            "pc_count": pc_count_val,
            "ps5_count": ps5_count_val,
            "other_devices": data.get("otherDevices") or "",
            "opening_hours_text": data.get("openingHours") or "",
            "website": website_input or "",
            "message": data.get("message") or "",
            "social": {"instagram": data.get("instagram") or ""},
        }

        result = db_main.cafes.insert_one(cafe_doc)
        cafe_doc["_id"] = result.inserted_id

        return {
            "status": "success",
            "cafe": map_cafe_doc(cafe_doc)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to create cafe: {e}"
        }


def get_cafe_detail_handler(cafe_id):
    """Retrieves a single gaming cafe by ID."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        from bson import ObjectId
        doc = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
        if not doc:
            return {"status": "error", "message": "Cafe not found."}
        # This view is always public (no auth) — never expose the owner's login email.
        return {"status": "success", "cafe": map_cafe_doc(doc, public=True)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve cafe detail: {e}"}


def update_cafe_handler(cafe_id, data):
    """Updates a gaming cafe's properties, including custom operating hours/slots."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        from bson import ObjectId
        update_fields = {}
        if "name" in data:
            err = input_validation.validate_text(data["name"], "Name", max_len=100)
            if err:
                return {"status": "error", "message": err}
            update_fields["name"] = data["name"]
        if "area" in data:
            err = input_validation.validate_text(data["area"], "Area", max_len=100)
            if err:
                return {"status": "error", "message": err}
            update_fields["area"] = data["area"]
        if "pricePerHour" in data or "price_per_hour" in data:
            raw_price = data.get("pricePerHour", data.get("price_per_hour"))
            price, err = input_validation.parse_bounded_number(raw_price, "Price Per Hour", min_val=0, max_val=100000)
            if err:
                return {"status": "error", "message": err}
            update_fields["price_per_hour"] = price
        if "distanceKm" in data:
            update_fields["distance_km"] = float(data["distanceKm"])
        if "latitude" in data:
            lat, err = input_validation.parse_bounded_number(data["latitude"], "Latitude", min_val=-90, max_val=90, is_float=True)
            if err:
                return {"status": "error", "message": err}
            update_fields["latitude"] = lat
        if "longitude" in data:
            lon, err = input_validation.parse_bounded_number(data["longitude"], "Longitude", min_val=-180, max_val=180, is_float=True)
            if err:
                return {"status": "error", "message": err}
            update_fields["longitude"] = lon
        if "specs" in data:
            update_fields["specs"] = data["specs"]
            update_fields["amenities"] = data["specs"]
        if "images" in data:
            update_fields["images"] = data["images"]
        if "slots" in data:
            update_fields["slots"] = data["slots"]
        if "address" in data:
            err = input_validation.validate_text(data["address"], "Address", max_len=400, required=False)
            if err:
                return {"status": "error", "message": err}
            update_fields["address"] = data["address"]
        if "city" in data:
            err = input_validation.validate_text(data["city"], "City", max_len=60, required=False)
            if err:
                return {"status": "error", "message": err}
            update_fields["city"] = data["city"]
        if "phone" in data:
            err = input_validation.validate_phone(data["phone"], required=False)
            if err:
                return {"status": "error", "message": err}
            update_fields["phone"] = data["phone"]
        if "contact_email" in data or "contactEmail" in data:
            email_val = data.get("contact_email", data.get("contactEmail"))
            if email_val:
                err = input_validation.validate_email(email_val)
                if err:
                    return {"status": "error", "message": err}
            update_fields["contact_email"] = email_val
        if "banner_url" in data or "bannerUrl" in data:
            url_val = data.get("banner_url", data.get("bannerUrl"))
            if url_val:
                err = input_validation.validate_url(url_val, "Banner URL")
                if err:
                    return {"status": "error", "message": err}
            update_fields["banner_url"] = url_val
        if "logo_url" in data or "logoUrl" in data:
            url_val = data.get("logo_url", data.get("logoUrl"))
            if url_val:
                err = input_validation.validate_url(url_val, "Logo URL")
                if err:
                    return {"status": "error", "message": err}
            update_fields["logo_url"] = url_val
        if "operating_hours" in data:
            update_fields["operating_hours"] = data["operating_hours"]
            update_fields["slots"] = generate_slots_from_hours(data["operating_hours"])
        if "operatingHours" in data:
            update_fields["operating_hours"] = data["operatingHours"]
            update_fields["slots"] = generate_slots_from_hours(data["operatingHours"])
        if "amenities" in data:
            update_fields["amenities"] = data["amenities"]
            update_fields["specs"] = data["amenities"]
        if "social" in data:
            social = data["social"] or {}
            if isinstance(social, dict):
                # Instagram is conventionally entered as a bare handle ("@cafe"), not a full
                # URL — both places that collect it in the frontends use that placeholder —
                # so it only gets a length cap, not URL-format validation. YouTube/Facebook
                # are validated as real URLs.
                instagram = social.get("instagram")
                if instagram:
                    err = input_validation.validate_text(instagram, "Instagram", max_len=80, required=False)
                    if err:
                        return {"status": "error", "message": err}
                for platform in ("youtube", "facebook"):
                    link = social.get(platform)
                    if link:
                        err = input_validation.validate_url(link, platform.capitalize() + " link")
                        if err:
                            return {"status": "error", "message": err}
            update_fields["social"] = social

        # Fields shared with the public partner-application form.
        if "ownerName" in data or "owner_name" in data:
            val = data.get("ownerName", data.get("owner_name"))
            if val:
                err = input_validation.validate_text(val, "Owner name", max_len=80, required=False)
                if err:
                    return {"status": "error", "message": err}
            update_fields["owner_name"] = val
        if "state" in data:
            if data["state"]:
                err = input_validation.validate_text(data["state"], "State", max_len=60, required=False)
                if err:
                    return {"status": "error", "message": err}
            update_fields["state"] = data["state"]
        if "pcCount" in data:
            val, err = input_validation.parse_bounded_number(data["pcCount"], "PC count", min_val=0, max_val=999, required=False)
            if err:
                return {"status": "error", "message": err}
            update_fields["pc_count"] = val
        if "ps5Count" in data:
            val, err = input_validation.parse_bounded_number(data["ps5Count"], "PS5 count", min_val=0, max_val=999, required=False)
            if err:
                return {"status": "error", "message": err}
            update_fields["ps5_count"] = val
        if "otherDevices" in data:
            if data["otherDevices"]:
                err = input_validation.validate_text(data["otherDevices"], "Other devices", max_len=200, required=False)
                if err:
                    return {"status": "error", "message": err}
            update_fields["other_devices"] = data["otherDevices"]
        if "openingHours" in data:
            if data["openingHours"]:
                err = input_validation.validate_text(data["openingHours"], "Opening hours", max_len=80, required=False)
                if err:
                    return {"status": "error", "message": err}
            update_fields["opening_hours_text"] = data["openingHours"]
        if "website" in data:
            if data["website"]:
                err = input_validation.validate_url(data["website"], "Website")
                if err:
                    return {"status": "error", "message": err}
            update_fields["website"] = data["website"]
        if "message" in data:
            if data["message"]:
                err = input_validation.validate_text(data["message"], "Message", max_len=800, required=False)
                if err:
                    return {"status": "error", "message": err}
            update_fields["message"] = data["message"]
        if "status" in data:
            # Same status vocabulary the super admin panel's badge already renders
            # (statusBadge in cafes.tsx) — this field never had backend handling at all
            # before, so "Suspend Hub"/"Activate Hub" silently 400'd on every click.
            enum_error = input_validation.validate_enum(
                data["status"], {"active", "pending", "suspended", "rejected"}, "Status"
            )
            if enum_error:
                return {"status": "error", "message": enum_error}
            update_fields["status"] = data["status"]

        if not update_fields:
            return {"status": "error", "message": "No valid fields to update."}

        res = db_main.cafes.update_one({"_id": ObjectId(cafe_id)}, {"$set": update_fields})
        if res.matched_count == 0:
            return {"status": "error", "message": "Cafe not found."}

        # If price_per_hour was updated, also update all associated rigs' hourly_price
        if "price_per_hour" in update_fields:
            db_main.rigs.update_many(
                {"cafe_id": str(cafe_id)},
                {"$set": {"hourly_price": update_fields["price_per_hour"]}}
            )

        updated_doc = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
        return {"status": "success", "cafe": map_cafe_doc(updated_doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to update cafe: {e}"}


def get_my_cafes_handler(owner_email, is_super_admin=False):
    """Retrieves all active cafes (for super admins) or only active cafes matching owner_email."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        query = {"is_deleted": {"$ne": True}}
        if not is_super_admin:
            query["owner_email"] = owner_email.strip().lower()

        docs = list(db_main.cafes.find(query))
        from . import subscriptions
        docs = [subscriptions._ensure_defaults(db_main, d) for d in docs]
        mapped_cafes = [map_cafe_doc(d) for d in docs]
        return {
            "status": "success",
            "cafes": mapped_cafes
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve my cafes: {e}"
        }


def get_razorpay_credentials_status_handler(cafe_id):
    """
    GET-side of the cafe owner's own Razorpay account settings — returns whether it's
    configured and the (public, safe-to-show) key_id, but NEVER the key_secret. The
    secret only ever flows one way: in via save_razorpay_credentials_handler, never back
    out to any frontend.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        doc = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
        if not doc:
            return {"status": "error", "message": "Cafe not found."}
        configured = bool(doc.get("razorpay_key_id") and doc.get("razorpay_key_secret_enc"))
        return {
            "status": "success",
            "configured": configured,
            "key_id": doc.get("razorpay_key_id") if configured else None,
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve Razorpay settings: {e}"}


def save_razorpay_credentials_handler(cafe_id, key_id, key_secret):
    """
    Saves the cafe owner's own Razorpay Key ID + Key Secret so their booking payments go
    straight into their own account instead of the platform's. key_secret is encrypted at
    rest with the same AES routine already used for TOTP secrets/phone numbers
    (auth_handler.encrypt_secret_key) — it's never stored or returned in plaintext.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        key_id = (key_id or "").strip()
        key_secret = (key_secret or "").strip()
        if not key_id or not key_secret:
            return {"status": "error", "message": "Both Key ID and Key Secret are required."}
        if not key_id.startswith(("rzp_live_", "rzp_test_")):
            return {"status": "error", "message": "That doesn't look like a valid Razorpay Key ID."}

        from .auth_handler import encrypt_secret_key, ENCRYPTION_KEY
        encrypted_secret = encrypt_secret_key(key_secret, ENCRYPTION_KEY)

        result = db_main.cafes.update_one(
            {"_id": ObjectId(cafe_id)},
            {"$set": {"razorpay_key_id": key_id, "razorpay_key_secret_enc": encrypted_secret}},
        )
        if result.matched_count == 0:
            return {"status": "error", "message": "Cafe not found."}
        return {"status": "success", "configured": True, "key_id": key_id}
    except Exception as e:
        return {"status": "error", "message": f"Failed to save Razorpay settings: {e}"}


def get_razorpay_password_status_handler(cafe_id, caller_email):
    """
    GET-side of the second-factor Razorpay password gate: tells the frontend whether to
    show "enter your Razorpay password to unlock" (already set) or "set a Razorpay
    password first" (an account that predates this feature, or never finished signup with
    one). caller_email is the AUTHENTICATED caller — a super admin viewing another
    owner's cafe always sees is_owner=False and bypasses the gate entirely client-side.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
        if not cafe:
            return {"status": "error", "message": "Cafe not found."}
        is_owner = caller_email == (cafe.get("owner_email") or "").strip().lower()
        if not is_owner:
            return {"status": "success", "is_owner": False, "has_password": None}
        admin = db_main.admins.find_one({"email": cafe["owner_email"]})
        has_password = bool(admin and admin.get("razorpay_password_hash"))
        return {"status": "success", "is_owner": True, "has_password": has_password}
    except Exception as e:
        return {"status": "error", "message": f"Failed to check Razorpay password status: {e}"}


def set_razorpay_password_handler(cafe_id, caller_email, new_password):
    """
    First-time setup only — for a cafe owner's account that predates this feature (signup
    never asked for a Razorpay password) or somehow has none. Refuses to overwrite an
    existing one; that's a deliberate limitation, not an oversight — there's no "forgot
    Razorpay password" recovery flow yet, so silently allowing a reset here would be a
    bypass of the very gate this password exists to enforce.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
        if not cafe:
            return {"status": "error", "message": "Cafe not found."}
        if caller_email != (cafe.get("owner_email") or "").strip().lower():
            return {"status": "error", "message": "Only this cafe's owner can set its Razorpay password."}

        from . import input_validation
        pw_error = input_validation.validate_password_strength(new_password or "")
        if pw_error:
            return {"status": "error", "message": pw_error}

        admin = db_main.admins.find_one({"email": cafe["owner_email"]})
        if not admin:
            # Should be unreachable in production — a cafe's owner_email always has a
            # matching db.admins document by the time they can reach this endpoint at all
            # (authenticate_admin_owner already required a valid admin JWT). Guarding
            # explicitly rather than letting update_one's no-op-when-no-match behavior
            # report a silent, misleading "success".
            return {"status": "error", "message": "Owner account not found."}
        if admin.get("razorpay_password_hash"):
            return {"status": "error", "message": "A Razorpay password is already set for this account."}

        from .auth_handler import ph
        db_main.admins.update_one(
            {"_id": admin["_id"]},
            {"$set": {"razorpay_password_hash": ph.hash(new_password)}},
        )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to set Razorpay password: {e}"}


def verify_razorpay_password_handler(cafe_id, caller_email, password):
    """
    The actual unlock check. A super admin viewing someone else's cafe (caller_email !=
    the cafe's own owner_email) bypasses this entirely — they already passed the stronger
    super-admin authentication upstream. Lockout mirrors the existing login-attempt
    lockout (same MAX_LOGIN_ATTEMPTS/LOGIN_LOCKOUT_MINUTES) so repeated guesses against
    this gate are bounded the same way login guesses already are.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
        if not cafe:
            return {"status": "error", "message": "Cafe not found."}

        owner_email = (cafe.get("owner_email") or "").strip().lower()
        if caller_email != owner_email:
            # Super admin (or anyone authenticate_admin_owner already let through who isn't
            # the literal owner) — bypass, no owner-only secret to check on their behalf.
            return {"status": "success", "verified": True}

        from datetime import datetime, timedelta, timezone
        from .auth_handler import verify_password, ph, IST, MAX_LOGIN_ATTEMPTS, LOGIN_LOCKOUT_MINUTES

        admin = db_main.admins.find_one({"email": owner_email})
        if not admin or not admin.get("razorpay_password_hash"):
            return {"status": "error", "needs_setup": True, "message": "No Razorpay password set yet for this account."}

        locked_until = admin.get("razorpay_password_locked_until")
        if locked_until:
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc).astimezone(IST)
            if datetime.now(IST) < locked_until:
                return {"status": "error", "message": "Too many incorrect attempts. Please try again later."}

        if not verify_password(admin["razorpay_password_hash"], password or ""):
            attempts = int(admin.get("razorpay_password_attempts", 0)) + 1
            update_fields: dict = {"razorpay_password_attempts": attempts}
            if attempts >= MAX_LOGIN_ATTEMPTS:
                update_fields["razorpay_password_locked_until"] = datetime.now(IST) + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
            db_main.admins.update_one({"_id": admin["_id"]}, {"$set": update_fields})
            return {"status": "error", "message": "Incorrect Razorpay password."}

        if admin.get("razorpay_password_attempts") or admin.get("razorpay_password_locked_until"):
            db_main.admins.update_one(
                {"_id": admin["_id"]},
                {"$unset": {"razorpay_password_attempts": "", "razorpay_password_locked_until": ""}},
            )
        return {"status": "success", "verified": True}
    except Exception as e:
        return {"status": "error", "message": f"Failed to verify Razorpay password: {e}"}


def delete_razorpay_credentials_handler(cafe_id):
    """Disconnects the cafe's own Razorpay account — booking payments fall back to the platform account again."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        result = db_main.cafes.update_one(
            {"_id": ObjectId(cafe_id)},
            {"$unset": {"razorpay_key_id": "", "razorpay_key_secret_enc": ""}},
        )
        if result.matched_count == 0:
            return {"status": "error", "message": "Cafe not found."}
        return {"status": "success", "configured": False}
    except Exception as e:
        return {"status": "error", "message": f"Failed to disconnect Razorpay: {e}"}


def delete_cafe_handler(cafe_id):
    """Soft deletes a gaming cafe from the database by setting is_deleted = True."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        result = db_main.cafes.update_one(
            {"_id": ObjectId(cafe_id)},
            {"$set": {"is_deleted": True}}
        )
        if result.matched_count == 0:
            return {"status": "error", "message": "Cafe not found or already deleted."}
        print(f"[KheloMore] Cafe {cafe_id} soft-deleted from database.")
        return {"status": "success", "message": "Cafe successfully removed from the network."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete cafe: {e}"}


def restore_cafe_handler(cafe_id):
    """Restores a soft-deleted gaming cafe by setting is_deleted = False."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}
    try:
        result = db_main.cafes.update_one(
            {"_id": ObjectId(cafe_id)},
            {"$set": {"is_deleted": False}}
        )
        if result.matched_count == 0:
            return {"status": "error", "message": "Cafe not found."}
        print(f"[KheloMore] Cafe {cafe_id} successfully restored.")
        return {"status": "success", "message": "Cafe successfully restored to the network."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to restore cafe: {e}"}


def parse_google_maps_url_handler(url):
    """Parses a Google Maps link (short or long redirect) to extract coordinates and place name."""
    import re
    import requests
    from urllib.parse import unquote, urlsplit

    if not url:
        return {"status": "error", "message": "URL parameter is required."}

    # SECURITY: this endpoint is public (no auth) so both the super-admin "Add Gaming
    # Cafe" form and the public "Partner Application" form can use it — a substring check
    # like `"goo.gl" in url` is an SSRF hole: a URL such as "http://internal-host/#goo.gl"
    # would pass it and this server would fetch whatever `internal-host` is. Only actually
    # follow the redirect when the URL's real hostname is Google's own shortener domain.
    try:
        hostname = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return {"status": "error", "message": "Invalid URL."}

    resolved_url = url
    if hostname in ("goo.gl", "maps.app.goo.gl"):
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            res = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            resolved_url = res.url
        except Exception as e:
            return {"status": "error", "message": f"Failed to resolve short link: {e}"}

    lat = None
    lon = None

    # Pattern 1: !3dLat!4dLon
    match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', resolved_url)
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
    else:
        # Pattern 2: @lat,lon
        match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', resolved_url)
        if match:
            lat = float(match.group(1))
            lon = float(match.group(2))
        else:
            # Pattern 3: q=lat,lon or query=lat,lon or ll=lat,lon
            match = re.search(r'[?&](q|query|ll)=(-?\d+\.\d+),(-?\d+\.\d+)', resolved_url)
            if match:
                lat = float(match.group(2))
                lon = float(match.group(3))

    address = ""
    # Pattern 1: /place/Name
    match_addr = re.search(r'/place/([^/?]+)', resolved_url)
    if match_addr:
        address = unquote(match_addr.group(1)).replace("+", " ")
        if "/@" in address:
            address = address.split("/@")[0]
    else:
        # Pattern 2: /maps/dir/Coordinates/Name
        match_addr = re.search(r'/maps/dir/[^/]+/([^/?]+)', resolved_url)
        if match_addr:
            address = unquote(match_addr.group(1)).replace("+", " ")

    if lat is None or lon is None:
        return {"status": "error", "message": "Could not extract coordinates from Google Maps link."}

    return {
        "status": "success",
        "latitude": lat,
        "longitude": lon,
        "address": address
    }


