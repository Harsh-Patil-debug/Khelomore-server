# KheloMore Gaming Hub — Backend

Django + DRF + MongoDB backend for the KheloMore Gaming Hub app.
Architecture follows the thin-views + Handlers pattern: views are pure HTTP wrappers, all business logic lives in Handlers/.

## Structure

```
backend/
├── manage.py
├── requirements.txt
├── .env                    ← copy from .env.example and fill in values
├── server/                 ← Django project config
│   ├── settings.py
│   ├── urls.py             ← mounts /api/v1/main/
│   ├── wsgi.py
│   └── asgi.py
└── gaming_project/
    └── main/
        ├── views.py        ← thin view wrappers (no logic here)
        ├── urls.py         ← all API route definitions
        └── Handlers/       ← all business logic lives here
            ├── db_connection.py
            ├── status_check.py
            ├── db_check.py
            ├── auth_handler.py
            ├── cafes_handler.py
            ├── bookings_handler.py
            ├── profile_handler.py
            └── notifications_handler.py
```

## Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt

# Run migrations (for Django internals)
python manage.py migrate

# Start dev server
python manage.py runserver 0.0.0.0:8000
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/main/status/` | Server health check |
| GET | `/api/v1/main/db/` | MongoDB ping |
| POST | `/api/v1/main/auth/register/` | Register new user |
| POST | `/api/v1/main/auth/login/` | Login and get JWT |
| GET | `/api/v1/main/cafes/` | List gaming centers |
| GET | `/api/v1/main/cafes/<id>/` | Gaming center detail |
| POST | `/api/v1/main/cafes/create/` | (Admin) Create center |
| PUT | `/api/v1/main/cafes/update/<id>/` | (Admin) Update center |
| DELETE | `/api/v1/main/cafes/delete/<id>/` | (Admin) Deactivate center |
| GET | `/api/v1/main/bookings/` | User's bookings |
| POST | `/api/v1/main/bookings/create/` | Create a booking |
| POST | `/api/v1/main/bookings/cancel/` | Cancel a booking |
| GET | `/api/v1/main/bookings/all/` | (Admin) All bookings |
| PUT | `/api/v1/main/bookings/status/<id>/` | (Admin) Update status |
| GET | `/api/v1/main/profile/` | Get user profile |
| POST | `/api/v1/main/profile/update/` | Update profile |
| GET | `/api/v1/main/notifications/` | Get notifications |
| POST | `/api/v1/main/notifications/register-token/` | Save push token |
| POST | `/api/v1/main/notifications/create/` | (Admin) Broadcast |
| DELETE | `/api/v1/main/notifications/delete/<id>/` | (Admin) Delete |

## Authentication

All protected endpoints require:
```
Authorization: Bearer <JWT token>
```
Get the token from `/auth/login/` or `/auth/register/`.

## MongoDB

Update `MONGO_URL` and `MONGO_DB_NAME` in `.env` when ready.
Collections used:
- `users`
- `cafes`
- `bookings`
- `notifications`
- `push_tokens`
