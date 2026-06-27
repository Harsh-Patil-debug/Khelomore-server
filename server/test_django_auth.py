import requests
import json

url = "http://localhost:8000/api/v1/main/auth/register/"
headers = {
    "Authorization": "Bearer km_admin_sec_3ea5c89fbf0e8ad9c922da1713d2f9b1740b2e8a15cfbceefea38b5fdf5e27a6",
    "Content-Type": "application/json"
}
# Send dummy payload that will fail decryption but at least hit views.py
data = {
    "gamertag": "dummy",
    "email": "dummy",
    "password": "dummy",
    "iv": "dummy"
}

try:
    response = requests.post(url, headers=headers, json=data)
    print(f"Localhost Status: {response.status_code}")
    print(f"Localhost Response: {response.text}")
except Exception as e:
    print(f"Localhost Connection failed: {e}")

ngrok_url = "https://twisting-stove-chief.ngrok-free.dev/api/v1/main/auth/register/"
try:
    response = requests.post(ngrok_url, headers=headers, json=data)
    print(f"Ngrok Status: {response.status_code}")
    print(f"Ngrok Response: {response.text}")
except Exception as e:
    print(f"Ngrok Connection failed: {e}")
