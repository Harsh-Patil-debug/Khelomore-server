# test_booking_manual_completion.py
# Regression tests: a booking whose scheduled slot time has passed must NOT be
# auto-completed — completion is admin-only now, via the "End Session" action. Covers
# both surfaces that used to do this independently: the bookings list
# (calculate_booking_status_and_time / get_user_bookings_handler) and the Live Floor
# session list (list_sessions_handler).

from datetime import datetime, timezone, timedelta

from bson import ObjectId

from .base import SecurityTestCase

IST = timezone(timedelta(hours=5, minutes=30))


class BookingManualCompletionTests(SecurityTestCase):
    def _make_rig(self, cafe_id, name="PC #01"):
        doc = {
            "cafe_id": cafe_id, "type": "PC", "name": name, "status": "available",
            "zone": "Standard", "hourly_price": 80,
        }
        result = self.db.rigs.insert_one(doc)
        return self.track("rigs", result.inserted_id)

    def _make_overdue_booking(self, cafe_id, db_status="Upcoming"):
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        start = (now - timedelta(hours=1)).strftime("%I:%M %p")
        end = (now - timedelta(minutes=30)).strftime("%I:%M %p")
        slot = f"{start} - {end}"
        doc = {
            "user_email": "walkin@bookmyconsole.com", "user_name": "SECTEST",
            "cafe_id": cafe_id, "cafe_name": "Sectest Cafe", "zone": "Standard",
            "date": today_str, "slots": [slot], "slot": slot,
            "price": 80, "code": "123456", "rig": "PC #01", "status": db_status,
        }
        result = self.db.bookings.insert_one(doc)
        self.track("bookings", result.inserted_id)
        return str(result.inserted_id)

    def test_overdue_booking_not_auto_completed_in_bookings_list(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._make_rig(cafe_id)
        booking_id = self._make_overdue_booking(cafe_id, db_status="Upcoming")

        resp = self.client.get(f"/api/v1/main/bookings/?cafe_id={cafe_id}", **self.auth_header(owner_token))
        self.assertEqual(resp.status_code, 200)
        item = next(b for b in resp.json()["bookings"] if b["id"] == booking_id)
        self.assertEqual(item["status"], "Upcoming")

        # And the DB itself must not have been silently rewritten either.
        doc = self.db.bookings.find_one({"_id": ObjectId(booking_id)})
        self.assertEqual(doc["status"], "Upcoming")

    def test_overdue_active_session_not_auto_completed_in_bookings_list(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._make_rig(cafe_id)
        booking_id = self._make_overdue_booking(cafe_id, db_status="Active")

        resp = self.client.get(f"/api/v1/main/bookings/?cafe_id={cafe_id}", **self.auth_header(owner_token))
        self.assertEqual(resp.status_code, 200)
        item = next(b for b in resp.json()["bookings"] if b["id"] == booking_id)
        self.assertEqual(item["status"], "Active")

        doc = self.db.bookings.find_one({"_id": ObjectId(booking_id)})
        self.assertEqual(doc["status"], "Active")

    def test_overdue_booking_not_auto_completed_on_live_floor(self):
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._make_rig(cafe_id)
        self._make_overdue_booking(cafe_id, db_status="Upcoming")

        resp = self.client.get(f"/api/v1/main/sessions/?cafe_id={cafe_id}", **self.auth_header(owner_token))
        self.assertEqual(resp.status_code, 200)
        sessions = resp.json()["sessions"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["status"], "reserved")

        rig = self.db.rigs.find_one({"cafe_id": cafe_id, "name": "PC #01"})
        self.assertEqual(rig["status"], "reserved")

    def test_manual_end_session_still_completes_a_booking(self):
        """The removed auto-expiry must not have taken the manual path down with it."""
        owner_email, owner_token = self.make_active_user(role="admin")
        cafe_id = self.make_cafe(owner_email=owner_email)
        self._make_rig(cafe_id)
        booking_id = self._make_overdue_booking(cafe_id, db_status="Active")

        resp = self.client.post(
            f"/api/v1/main/sessions/{booking_id}/end/", format="json", **self.auth_header(owner_token)
        )
        self.assertEqual(resp.status_code, 200)

        doc = self.db.bookings.find_one({"_id": ObjectId(booking_id)})
        self.assertEqual(doc["status"], "Completed")
        rig = self.db.rigs.find_one({"cafe_id": cafe_id, "name": "PC #01"})
        self.assertEqual(rig["status"], "available")
