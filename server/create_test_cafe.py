import sys
import os
import re
from pathlib import Path

# Setup Django environment so we can import models/handlers
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')

# Add server path to sys.path
sys.path.append(str(Path(__file__).resolve().parent))

from gaming_project.main.Handlers.db_connection import get_db

def parse_google_maps_url(url):
    """
    Parses a Google Maps URL (or short redirect link) to extract coordinates and place/address name.
    """
    try:
        import requests
        from urllib.parse import unquote
    except ImportError:
        print("Required libraries missing. Run 'pip install requests' first.")
        return None

    # Resolve redirect if it's a short link
    if "maps.app.goo.gl" in url or "goo.gl" in url:
        try:
            print("Resolving Google Maps short link redirect...")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            res = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            url = res.url
        except Exception as e:
            print(f"Error resolving Google Maps link: {e}")
            return None

    # Try to extract latitude and longitude
    # Pattern 1: !3dLat!4dLon
    match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', url)
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
        return {
            "lat": lat,
            "lon": lon,
            "address": extract_address_from_url(url)
        }

    # Pattern 2: @lat,lon
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
        return {
            "lat": lat,
            "lon": lon,
            "address": extract_address_from_url(url)
        }
    
    # Pattern 3: q=lat,lon or query=lat,lon or ll=lat,lon
    match = re.search(r'[?&](q|query|ll)=(-?\d+\.\d+),(-?\d+\.\d+)', url)
    if match:
        lat = float(match.group(2))
        lon = float(match.group(3))
        return {
            "lat": lat,
            "lon": lon,
            "address": extract_address_from_url(url)
        }

    return None

def extract_address_from_url(url):
    """Extracts and decodes the place/address name from the Google Maps URL path."""
    from urllib.parse import unquote
    
    # Pattern 1: /place/Name
    match = re.search(r'/place/([^/?]+)', url)
    if match:
        place_name = unquote(match.group(1)).replace("+", " ")
        if "/@" in place_name:
            place_name = place_name.split("/@")[0]
        return place_name
        
    # Pattern 2: /maps/dir/Coordinates/Name
    match = re.search(r'/maps/dir/[^/]+/([^/?]+)', url)
    if match:
        place_name = unquote(match.group(1)).replace("+", " ")
        return place_name

    return ""

def create_cafe():
    db = get_db()
    if db is None:
        print("Failed to connect to MongoDB.")
        return
        
    print("\n--- List New Cafe (Super Admin Emulator) ---")
    
    name = input("Enter Cafe Name: ").strip()
    while not name:
        name = input("Cafe Name is required. Enter Cafe Name: ").strip()

    area = input("Enter Area (e.g. Nerul, Bandra): ").strip()
    while not area:
        area = input("Area is required. Enter Area: ").strip()

    price_input = input("Enter Price per Hour (₹, default 150): ").strip()
    price_per_hour = int(price_input) if price_input else 150

    owner_email = input("Enter Cafe Owner Email to authorize: ").strip().lower()
    while not owner_email:
        owner_email = input("Owner email is required to link dashboard control: ").strip().lower()

    city = input("Enter City (e.g. Mumbai): ").strip()
    phone = input("Enter Phone Number: ").strip()

    # Location input flow
    print("\nSelect location entry method:")
    print("1. Enter address details manually (and optional coordinates)")
    print("2. Import coordinates & address from Google Maps link")
    choice = input("Enter choice (1 or 2, default 1): ").strip()

    address = ""
    latitude = None
    longitude = None

    if choice == "2":
        maps_link = input("Paste Google Maps link (e.g., https://maps.app.goo.gl/xxx): ").strip()
        while not maps_link:
            maps_link = input("Google Maps link is required for Option 2: ").strip()
            
        location_data = parse_google_maps_url(maps_link)
        if location_data:
            latitude = location_data["lat"]
            longitude = location_data["lon"]
            address = location_data["address"]
            print(f"\nSuccessfully extracted details from Google Maps:")
            print(f" - Latitude: {latitude}")
            print(f" - Longitude: {longitude}")
            print(f" - Extracted Address: '{address}'")
            
            confirm_addr = input("Use this address? (Press Enter to confirm, or type custom address): ").strip()
            if confirm_addr:
                address = confirm_addr
        else:
            print("\nCould not extract coordinates from Google Maps link. Falling back to manual entry.")
            choice = "1"

    if choice != "2":
        address = input("Enter Address: ").strip()
        while not address:
            address = input("Address is required. Enter Address: ").strip()

        # Try to resolve coordinates automatically using geopy (free Nominatim API)
        auto_resolved = False
        try:
            from geopy.geocoders import Nominatim
            print("Resolving coordinates automatically using geopy Nominatim...")
            geolocator = Nominatim(user_agent="khelomore_super_admin")
            query = f"{address}, {city}" if city else address
            loc = geolocator.geocode(query, timeout=10)
            if loc:
                latitude = loc.latitude
                longitude = loc.longitude
                print(f"Success: Latitude={latitude}, Longitude={longitude}")
                auto_resolved = True
        except Exception as e:
            pass

        if not auto_resolved:
            print("Auto-resolution bypassed or failed. Please enter coordinates manually:")
            lat_input = input("Enter Latitude (e.g. 19.0330, or press Enter for default): ").strip()
            latitude = float(lat_input) if lat_input else None
            
            lon_input = input("Enter Longitude (e.g. 73.0190, or press Enter for default): ").strip()
            longitude = float(lon_input) if lon_input else None

    # Default coordinates to Nerul centre if not specified
    if latitude is None or longitude is None:
        latitude = 19.0330
        longitude = 73.0190

    specs_input = input("\nEnter System Specs (comma-separated, e.g. RTX 4070 Rig, PS5 Console): ").strip()
    specs = [s.strip() for s in specs_input.split(",") if s.strip()] if specs_input else ["RTX 4070 Rig", "PS5 Console"]
    
    rating_input = input("Enter Initial Rating (default 5.0): ").strip()
    rating = float(rating_input) if rating_input else 5.0

    reviews_input = input("Enter Initial Reviews Count (default 1): ").strip()
    reviews = int(reviews_input) if reviews_input else 1
    
    images_input = input("Enter Image URLs (comma-separated, or press Enter for default): ").strip()
    images = [i.strip() for i in images_input.split(",") if i.strip()] if images_input else [
        "https://images.unsplash.com/photo-1542751371-adc38448a05e?q=80&w=600"
    ]
    
    cafe_doc = {
        "name": name,
        "area": area,
        "price_per_hour": price_per_hour,
        "owner_email": owner_email,
        "address": address,
        "city": city,
        "phone": phone,
        "specs": specs,
        "rating": rating,
        "reviews": reviews,
        "images": images,
        "latitude": latitude,
        "longitude": longitude
    }
    
    res = db.cafes.insert_one(cafe_doc)
    print(f"\nSuccess! Listed cafe '{name}' (ID: {res.inserted_id}) assigned to owner email '{owner_email}'.")
    print(f"Coordinates stored: ({latitude}, {longitude})")
    print("Now you can visit the admin dashboard, click Sign Up, and register using this email!")

if __name__ == "__main__":
    create_cafe()
