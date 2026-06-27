import sys
from pathlib import Path

# Ensure the parent directory is on the path so we can import Handlers
sys.path.append(str(Path(__file__).resolve().parent))

from gaming_project.main.Handlers.db_connection import db_main

email = "harshdpatil2007@gmail.com"
print("--- Checking Users Collection ---")
user = db_main.users.find_one({"email": email})
if user:
    print(f"Found in users: id={user['_id']}, email={user.get('email')}, role={user.get('role')}, status={user.get('status')}")
else:
    print("Not found in users.")

print("\n--- Checking Admins Collection ---")
admin = db_main.admins.find_one({"email": email})
if admin:
    print(f"Found in admins: id={admin['_id']}, email={admin.get('email')}, role={admin.get('role')}, status={admin.get('status')}")
else:
    print("Not found in admins.")
