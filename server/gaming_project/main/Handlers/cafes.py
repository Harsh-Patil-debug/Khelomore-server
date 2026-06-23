# cafes.py
# Handlers for managing gaming cafes, including real-time distance calculations

import os
import json
import math
import random
import cloudinary
import cloudinary.uploader
from .db_connection import get_db

# Configure Cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", "dghp9tq9m"),
    api_key=os.getenv("CLOUDINARY_API_KEY", "631388716584283"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET", "your_api_secret_placeholder"),
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


def map_cafe_doc(doc, user_lat=None, user_lon=None):
    """Maps a MongoDB cafe document to the format expected by the React Native frontend."""
    
    calculated_distance = None
    if user_lat is not None and user_lon is not None:
        cafe_lat = doc.get("latitude")
        cafe_lon = doc.get("longitude")
        if cafe_lat is not None and cafe_lon is not None:
            calculated_distance = calculate_haversine_distance(user_lat, user_lon, cafe_lat, cafe_lon)
            
    # Fallback to the static distance stored in the database if user coordinates are not sent
    distance_km = calculated_distance if calculated_distance is not None else float(doc.get("distance_km", 0.0))
    
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "distanceKm": distance_km,
        "latitude": float(doc.get("latitude")) if doc.get("latitude") is not None else None,
        "longitude": float(doc.get("longitude")) if doc.get("longitude") is not None else None,
        "rating": float(doc.get("rating", 0.0)),
        "reviews": int(doc.get("reviews", 0)),
        "area": doc.get("area", ""),
        "specs": doc.get("specs", []),
        "pricePerHour": int(doc.get("price_per_hour", 0)),
        "images": doc.get("images", [])
    }


def get_cafes_handler(latitude=None, longitude=None):
    """Retrieves all gaming cafes from the database. Seeds the DB if empty."""
    db_main = get_db()
    if db_main is None:
        return {
            "status": "error",
            "message": "MongoDB connection is not established."
        }

    try:
        # Check if the cafes collection is empty
        if db_main.cafes.count_documents({}) == 0:
            db_main.cafes.insert_many(SEED_CAFES)
            print("[KheloMore] Database seeded with default gaming cafes.")

        # Parse request coordinates if present
        user_lat = None
        user_lon = None
        if latitude is not None and longitude is not None:
            try:
                user_lat = float(latitude)
                user_lon = float(longitude)
            except ValueError:
                pass

        # Retrieve all cafes and sort by calculated distance (nearest first)
        docs = list(db_main.cafes.find({}))
        mapped_cafes = [map_cafe_doc(d, user_lat, user_lon) for d in docs]
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
        price_per_hour = data.get("pricePerHour")
        distance_km = data.get("distanceKm")
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        specs = data.get("specs", [])
        images = data.get("images", [])

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

        # Handle Cloudinary upload
        image_url = None
        if files and "image" in files:
            image_file = files["image"]
            try:
                upload_result = cloudinary.uploader.upload(image_file)
                image_url = upload_result.get("secure_url")
                print(f"[Cloudinary] Successfully uploaded image to: {image_url}")
            except Exception as upload_err:
                print(f"[Cloudinary] Image upload failed: {upload_err}")

        # Construct final images list
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

        # Construct MongoDB document
        cafe_doc = {
            "name": name,
            "area": area,
            "price_per_hour": price_per_hour,
            "distance_km": distance_km,
            "latitude": latitude,
            "longitude": longitude,
            "rating": 5.0,
            "reviews": 1,
            "specs": specs,
            "images": final_images
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
