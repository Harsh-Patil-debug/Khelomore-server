# test_sessions.py
# Regression test for the Live Floor / Systems page rig-status desync: a same-day booking
# that hasn't started yet must still mark the rig "reserved", not just once its slot window
# has actually begun.

from datetime import datetime, timezone, timedelta

from .base import SecurityTestCase

IST = timezone(timedelta(hours=5, minutes=30))


class FloorSessionRigStatusSyncTests(SecurityTestCase):
    def _make_rig(self, cafe_id, name="PC #01"):
        doc = {
            "cafe_id": cafe_id, "type": "PC", "name": name, "status": "available",
            "zone": "Standard", "hourly_price": 80,
        }
        result = self.db.rigs.insert_one(doc)
        return self.track("rigs", result.inserted_id)

    def test_todays_not_yet_started_booking_marks_rig_reserved(self):
        """A booking for later today (slot hasn't started) must mark the rig reserved —
        otherwise the Systems page and Live Floor summary tiles both show "Available" for
        a PC that's already booked, letting the owner double-book or start a walk-in on it."""
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._make_rig(cafe_id)

        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        doc = {
            "user_email": "walkin@khelomore.com", "user_name": "SECTEST",
            "cafe_id": cafe_id, "cafe_name": "Sectest Cafe", "zone": "Standard",
            "date": today_str, "slots": ["11:58 PM - 11:59 PM"], "slot": "11:58 PM - 11:59 PM",
            "price": 80, "code": "123456", "rig": "PC #01", "status": "Upcoming",
        }
        result = self.db.bookings.insert_one(doc)
        self.track("bookings", result.inserted_id)

        resp = self.client.get(f"/api/v1/main/sessions/?cafe_id={cafe_id}", **self.auth_header(owner_token))
        self.assertEqual(resp.status_code, 200)

        rig = self.db.rigs.find_one({"cafe_id": cafe_id, "name": "PC #01"})
        self.assertEqual(rig["status"], "reserved")

    def test_booking_for_a_future_date_does_not_mark_rig_reserved_today(self):
        """A booking for a different (future) date must not block today's use of the rig."""
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._make_rig(cafe_id)

        doc = {
            "user_email": "walkin@khelomore.com", "user_name": "SECTEST",
            "cafe_id": cafe_id, "cafe_name": "Sectest Cafe", "zone": "Standard",
            "date": "2099-01-01", "slots": ["10:00 AM - 11:00 AM"], "slot": "10:00 AM - 11:00 AM",
            "price": 80, "code": "123456", "rig": "PC #01", "status": "Upcoming",
        }
        result = self.db.bookings.insert_one(doc)
        self.track("bookings", result.inserted_id)

        resp = self.client.get(f"/api/v1/main/sessions/?cafe_id={cafe_id}", **self.auth_header(owner_token))
        self.assertEqual(resp.status_code, 200)

        rig = self.db.rigs.find_one({"cafe_id": cafe_id, "name": "PC #01"})
        self.assertEqual(rig["status"], "available")
