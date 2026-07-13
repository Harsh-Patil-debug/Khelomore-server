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

        # Profile specific fields
        "banner_url": doc.get("banner_url") or (images_list[0] if images_list else ""),
        "logo_url": doc.get("logo_url", ""),
        "operating_hours": doc.get("operating_hours") or {"open": "10:00", "close": "23:00"},
        "amenities": doc.get("amenities") or specs_list,
        "social": doc.get("social") or {"instagram": "", "youtube": "", "facebook": ""},
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
        query = {} if include_deleted else {"is_deleted": {"$ne": True}}
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

        try:
            price_per_hour = int(price_per_hour)
        except ValueError:
            return {
                "status": "error",
                "message": "Price Per Hour must be an integer."
            }

        # Parse coordinates
        try:
            latitude = float(latitude) if latitude is not None else None
        except ValueError:
            latitude = None

        try:
            longitude = float(longitude) if longitude is not None else None
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

        final_images.extend(text_images)

        if not final_images:
            final_images = ["https://images.unsplash.com/photo-1542751371-adc38448a05e?q=80&w=600"]

        # Construct MongoDB document
        try:
            rating_val = float(data.get("rating") or 5.0)
        except (ValueError, TypeError):
            rating_val = 5.0

        try:
            reviews_val = int(data.get("reviews") or data.get("review_count") or 0)
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
            "owner_email": data.get("owner_email") or data.get("ownerEmail") or data.get("contact_email") or data.get("contactEmail") or "",
            "address": data.get("address") or "",
            "city": data.get("city") or "",
            "phone": data.get("phone") or ""
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
            update_fields["name"] = data["name"]
        if "area" in data:
            update_fields["area"] = data["area"]
        if "pricePerHour" in data:
            update_fields["price_per_hour"] = int(data["pricePerHour"])
        if "price_per_hour" in data:
            update_fields["price_per_hour"] = int(data["price_per_hour"])
        if "distanceKm" in data:
            update_fields["distance_km"] = float(data["distanceKm"])
        if "latitude" in data:
            update_fields["latitude"] = float(data["latitude"])
        if "longitude" in data:
            update_fields["longitude"] = float(data["longitude"])
        if "specs" in data:
            update_fields["specs"] = data["specs"]
            update_fields["amenities"] = data["specs"]
        if "images" in data:
            update_fields["images"] = data["images"]
        if "slots" in data:
            update_fields["slots"] = data["slots"]
        if "address" in data:
            update_fields["address"] = data["address"]
        if "city" in data:
            update_fields["city"] = data["city"]
        if "phone" in data:
            update_fields["phone"] = data["phone"]
        if "contact_email" in data:
            update_fields["contact_email"] = data["contact_email"]
        if "contactEmail" in data:
            update_fields["contact_email"] = data["contactEmail"]
        if "banner_url" in data:
            update_fields["banner_url"] = data["banner_url"]
        if "bannerUrl" in data:
            update_fields["banner_url"] = data["bannerUrl"]
        if "logo_url" in data:
            update_fields["logo_url"] = data["logo_url"]
        if "logoUrl" in data:
            update_fields["logo_url"] = data["logoUrl"]
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
            update_fields["social"] = data["social"]

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
    from urllib.parse import unquote

    if not url:
        return {"status": "error", "message": "URL parameter is required."}

    resolved_url = url
    if "maps.app.goo.gl" in url or "goo.gl" in url:
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


