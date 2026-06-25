# rigs.py
# Handlers for managing PC/Console hardware rigs in KheloMore Gaming Hub

from bson import ObjectId
from .db_connection import get_db

# Default rigs template to seed for each cafe
SEED_RIG_TEMPLATES = [
    {"type": "PC", "name": "PC #01", "spec": "RTX 4090 · 360Hz"},
    {"type": "PC", "name": "PC #02", "spec": "RTX 4090 · 360Hz"},
    {"type": "PC", "name": "PC #03", "spec": "RTX 4090 · 360Hz"},
    {"type": "PC", "name": "PC #04", "spec": "RTX 4080 · 240Hz"},
    {"type": "PC", "name": "PC #05", "spec": "RTX 4080 · 240Hz"},
    {"type": "PC", "name": "PC #06", "spec": "RTX 4080 · 240Hz"},
    {"type": "PC", "name": "PC #07", "spec": "RTX 4070 · 240Hz"},
    {"type": "PS5", "name": "PS5 Pro #01", "spec": "DualSense Edge · 4K"},
    {"type": "PS5", "name": "PS5 Pro #02", "spec": "DualSense Edge · 4K"},
]

def map_rig_doc(doc):
    """Maps a MongoDB rig document to the format expected by the frontend."""
    return {
        "id": str(doc["_id"]),
        "cafeId": doc.get("cafe_id"),
        "type": doc.get("type", "PC"),
        "name": doc.get("name", ""),
        "spec": doc.get("spec", "")
    }

def get_rigs_handler(cafe_id=None):
    """Retrieves all rigs or filters by cafe_id. Seeds the database if empty."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        # Auto-seed if the rigs collection is completely empty
        if db_main.rigs.count_documents({}) == 0:
            cafes = list(db_main.cafes.find({}))
            seeded_count = 0
            if cafes:
                rigs_to_insert = []
                for cafe in cafes:
                    cafe_id_str = str(cafe["_id"])
                    for template in SEED_RIG_TEMPLATES:
                        rigs_to_insert.append({
                            "cafe_id": cafe_id_str,
                            "type": template["type"],
                            "name": template["name"],
                            "spec": template["spec"]
                        })
                if rigs_to_insert:
                    db_main.rigs.insert_many(rigs_to_insert)
                    seeded_count = len(rigs_to_insert)
            
            # Also seed some global/unassigned rigs just in case
            global_rigs = []
            for template in SEED_RIG_TEMPLATES:
                global_rigs.append({
                    "cafe_id": None,
                    "type": template["type"],
                    "name": f"Global {template['name']}",
                    "spec": template["spec"]
                })
            db_main.rigs.insert_many(global_rigs)
            seeded_count += len(global_rigs)
            
            print(f"[KheloMore] Auto-seeded {seeded_count} hardware rigs in MongoDB.")

        # Build query
        query = {}
        if cafe_id:
            query["cafe_id"] = cafe_id

        docs = list(db_main.rigs.find(query))
        mapped = [map_rig_doc(d) for d in docs]
        return {"status": "success", "rigs": mapped}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve rigs: {e}"}

def create_rig_handler(data):
    """Creates a new hardware rig (PC or PS5) in MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        rig_type = data.get("type")
        name = data.get("name")
        spec = data.get("spec")
        cafe_id = data.get("cafeId") or data.get("cafe_id")

        if not rig_type or not name:
            return {"status": "error", "message": "Rig 'type' and 'name' are required fields."}

        if rig_type not in ["PC", "PS5"]:
            return {"status": "error", "message": "Rig type must be either 'PC' or 'PS5'."}

        rig_doc = {
            "cafe_id": cafe_id,
            "type": rig_type,
            "name": name,
            "spec": spec or ""
        }

        result = db_main.rigs.insert_one(rig_doc)
        rig_doc["_id"] = result.inserted_id

        print(f"[KheloMore] Created rig: {rig_doc['name']} for cafe: {rig_doc['cafe_id']}")
        return {"status": "success", "rig": map_rig_doc(rig_doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to create rig: {e}"}

def get_rig_detail_handler(rig_id):
    """Retrieves a single hardware rig by its ID."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}

        doc = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        if not doc:
            return {"status": "error", "message": "Rig not found."}

        return {"status": "success", "rig": map_rig_doc(doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve rig detail: {e}"}

def update_rig_handler(rig_id, data):
    """Updates an existing hardware rig's fields in MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}

        # Fields allowed to update
        update_fields = {}
        if "type" in data:
            if data["type"] not in ["PC", "PS5"]:
                return {"status": "error", "message": "Rig type must be either 'PC' or 'PS5'."}
            update_fields["type"] = data["type"]
        if "name" in data:
            update_fields["name"] = data["name"]
        if "spec" in data:
            update_fields["spec"] = data["spec"]
        if "cafeId" in data or "cafe_id" in data:
            update_fields["cafe_id"] = data.get("cafeId") or data.get("cafe_id")

        if not update_fields:
            return {"status": "error", "message": "No valid fields to update were provided."}

        result = db_main.rigs.update_one(
            {"_id": ObjectId(rig_id)},
            {"$set": update_fields}
        )

        if result.matched_count == 0:
            return {"status": "error", "message": "Rig not found."}

        updated_doc = db_main.rigs.find_one({"_id": ObjectId(rig_id)})
        print(f"[KheloMore] Updated rig: {rig_id}")
        return {"status": "success", "rig": map_rig_doc(updated_doc)}
    except Exception as e:
        return {"status": "error", "message": f"Failed to update rig: {e}"}

def delete_rig_handler(rig_id):
    """Deletes a hardware rig from MongoDB."""
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        if not ObjectId.is_valid(rig_id):
            return {"status": "error", "message": "Invalid rig ID format."}

        result = db_main.rigs.delete_one({"_id": ObjectId(rig_id)})
        if result.deleted_count == 0:
            return {"status": "error", "message": "Rig not found."}

        print(f"[KheloMore] Deleted rig: {rig_id}")
        return {"status": "success", "message": "Rig successfully deleted."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to delete rig: {e}"}
