import sys
import os
from pathlib import Path

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
sys.path.append(os.getcwd())

from gaming_project.main.Handlers.db_connection import get_db

def add_red_zone():
    db = get_db()
    if db is None:
        print("Failed to connect to MongoDB.")
        return
        
    email = "harshdpatil2007@gamil.com"
    price = 80
    
    # 1. Insert Red Zone Gaming Cafe
    cafe_doc = {
        "name": "Red Zone Gaming Cafe",
        "area": "Sector 3, Nerul",
        "price_per_hour": price,
        "owner_email": email,
        "address": "shop number - b01, building number - 14, NL2 Rd, near Rangoli Hotel, opposite to Sai Baba mandir, Nerul East, Sector 3, Nerul, Navi Mumbai, Maharashtra 400706",
        "city": "Mumbai",
        "phone": "088506 98820",
        "specs": ["RTX 4090", "240Hz+", "VIP Lounge", "PS5"],
        "rating": 5.0,
        "reviews": 98,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075374/t9y65e3kk7iwalkaw2gr.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075375/jivkorclim8av3didmvb.jpg"
        ],
        "latitude": 19.0418,
        "longitude": 73.0208
    }
    
    # Check if cafe already exists, if so delete to prevent duplicates
    db.cafes.delete_many({"name": "Red Zone Gaming Cafe"})
    res = db.cafes.insert_one(cafe_doc)
    cafe_id = str(res.inserted_id)
    print(f"Successfully inserted cafe '{cafe_doc['name']}' with ID: {cafe_id}")
    
    # 2. Insert rigs for Red Zone Gaming Cafe
    db.rigs.delete_many({"cafe_id": cafe_id})
    rig_templates = [
        {"type": "PC", "name": "PC #01", "spec": "RTX 4090 · 360Hz"},
        {"type": "PC", "name": "PC #02", "spec": "RTX 4090 · 360Hz"},
        {"type": "PC", "name": "PC #03", "spec": "RTX 4090 · 360Hz"},
        {"type": "PC", "name": "PC #04", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #05", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #06", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #07", "spec": "RTX 4070 · 240Hz"},
        {"type": "PS5", "name": "PS5 #01", "spec": "DualSense Edge · 4K"},
        {"type": "PS5", "name": "PS5 #02", "spec": "DualSense Edge · 4K"},
    ]
    
    rigs_to_insert = []
    for r in rig_templates:
        rigs_to_insert.append({
            "cafe_id": cafe_id,
            "type": r["type"],
            "name": r["name"],
            "spec": r["spec"]
        })
        
    db.rigs.insert_many(rigs_to_insert)
    print(f"Seeded {len(rigs_to_insert)} rigs for Red Zone Gaming Cafe.")

def add_play_hive():
    db = get_db()
    if db is None:
        print("Failed to connect to MongoDB.")
        return
        
    email = "vmingale2007@gmail.com"
    price = 80
    
    # 1. Insert Play Hive Gaming Cafe
    cafe_doc = {
        "name": "Play Hive Gaming Cafe",
        "area": "Sector 23, Nerul",
        "price_per_hour": price,
        "owner_email": email,
        "address": "Shop no 1, Play hive, near Darave, Sector 23, Nerul East, talav, Nerul, Navi Mumbai, Maharashtra 400706",
        "city": "Navi Mumbai",
        "phone": "098671 64231",
        "specs": ["RTX 4080", "240Hz", "PS5", "VR Booth"],
        "rating": 4.9,
        "reviews": 50,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782199130/rogue_circuit_img1.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782199131/rogue_circuit_img2.jpg"
        ],
        "latitude": 19.0261115,
        "longitude": 73.0195576
    }
    
    # Check if cafe already exists, if so delete to prevent duplicates
    db.cafes.delete_many({"name": "Play Hive Gaming Cafe"})
    res = db.cafes.insert_one(cafe_doc)
    cafe_id = str(res.inserted_id)
    print(f"Successfully inserted cafe '{cafe_doc['name']}' with ID: {cafe_id}")
    
    # 2. Insert rigs for Play Hive Gaming Cafe
    db.rigs.delete_many({"cafe_id": cafe_id})
    rig_templates = [
        {"type": "PC", "name": "PC #01", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #02", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #03", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #04", "spec": "RTX 4070 · 240Hz"},
        {"type": "PC", "name": "PC #05", "spec": "RTX 4070 · 240Hz"},
        {"type": "PS5", "name": "PS5 #01", "spec": "DualSense Edge · 4K"},
        {"type": "PS5", "name": "PS5 #02", "spec": "DualSense Edge · 4K"},
    ]
    
    rigs_to_insert = []
    for r in rig_templates:
        rigs_to_insert.append({
            "cafe_id": cafe_id,
            "type": r["type"],
            "name": r["name"],
            "spec": r["spec"]
        })
        
    db.rigs.insert_many(rigs_to_insert)
    print(f"Seeded {len(rigs_to_insert)} rigs for Play Hive Gaming Cafe.")

def add_vortex_gaming():
    db = get_db()
    if db is None:
        print("Failed to connect to MongoDB.")
        return
        
    email = "shrutidpatil0309@gmail.com"
    price = 80
    
    # 1. Insert Vortex Gaming
    cafe_doc = {
        "name": "Vortex Gaming",
        "area": "Sector 10, New Panvel",
        "price_per_hour": price,
        "owner_email": email,
        "address": "1st floor, pragapati ornate, shop no, 5-6, Sector-10, New Panvel East, Panvel, Maharashtra 410206",
        "city": "Panvel",
        "phone": "083695 10314",
        "specs": ["RTX 4080", "165Hz", "Console Lounge", "VR Booth"],
        "rating": 4.8,
        "reviews": 29,
        "images": [
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075378/h8vobzu6tsac90wd4wn2.jpg",
            "https://res.cloudinary.com/dx1ulvuqy/image/upload/v1782075378/xgdl5iysbxyoqb4npofp.jpg"
        ],
        "latitude": 19.0065684,
        "longitude": 73.1087711
    }
    
    # Check if cafe already exists, if so delete to prevent duplicates
    db.cafes.delete_many({"name": "Vortex Gaming"})
    res = db.cafes.insert_one(cafe_doc)
    cafe_id = str(res.inserted_id)
    print(f"Successfully inserted cafe '{cafe_doc['name']}' with ID: {cafe_id}")
    
    # 2. Insert rigs for Vortex Gaming
    db.rigs.delete_many({"cafe_id": cafe_id})
    rig_templates = [
        {"type": "PC", "name": "PC #01", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #02", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #03", "spec": "RTX 4080 · 240Hz"},
        {"type": "PC", "name": "PC #04", "spec": "RTX 4070 · 165Hz"},
        {"type": "PC", "name": "PC #05", "spec": "RTX 4070 · 165Hz"},
        {"type": "PS5", "name": "PS5 #01", "spec": "DualSense Edge · 4K"},
    ]
    
    rigs_to_insert = []
    for r in rig_templates:
        rigs_to_insert.append({
            "cafe_id": cafe_id,
            "type": r["type"],
            "name": r["name"],
            "spec": r["spec"]
        })
        
    db.rigs.insert_many(rigs_to_insert)
    print(f"Seeded {len(rigs_to_insert)} rigs for Vortex Gaming.")

if __name__ == "__main__":
    add_red_zone()
    add_play_hive()
    add_vortex_gaming()
