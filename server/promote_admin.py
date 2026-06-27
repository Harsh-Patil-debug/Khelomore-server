import sys
from pathlib import Path

# Ensure the parent directory is on the path so we can import Handlers
sys.path.append(str(Path(__file__).resolve().parent))

from gaming_project.main.Handlers.db_connection import db_main

email = "harshdpatil2007@gmail.com"
print(f"[Admin Promotion] Connecting to MongoDB...")
try:
    user = db_main.users.find_one({"email": email})
    if not user:
        print(f"[Admin Promotion] Error: User with email '{email}' not found in the users collection.")
        sys.exit(1)
    
    current_role = user.get('role', 'user')
    print(f"[Admin Promotion] Found user: {user.get('gamertag')} ({user.get('email')}) - Current role: {current_role}")
    
    res = db_main.users.update_one(
        {"email": email},
        {"$set": {"role": "admin"}}
    )
    if res.modified_count > 0 or res.matched_count > 0:
        print(f"[Admin Promotion] Successfully promoted '{email}' to 'admin' in MongoDB!")
    else:
        print(f"[Admin Promotion] User '{email}' was already updated.")
except Exception as e:
    print(f"[Admin Promotion] An error occurred: {e}")
