# sessions.py
# Handlers for managing real-time game sessions on the Live Floor

import os
import random
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from .db_connection import get_db
from . import input_validation

IST = timezone(timedelta(hours=5, minutes=30))

def parse_slot_times(date_str: str, slots: list) -> tuple:
    """
    Parses start and end times for a list of slots relative to a date string.
    Returns (earliest_start, latest_end) as localized timezone-aware datetimes.
    """
    start_times = []
    end_times = []
    for slot in slots:
        parts = slot.split("-")
        if len(parts) == 2:
            try:
                st = datetime.strptime(f"{date_str} {parts[0].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                et = datetime.strptime(f"{date_str} {parts[1].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                if et <= st:
                    et += timedelta(days=1)
                start_times.append(st)
                end_times.append(et)
            except Exception:
                pass
    if not start_times:
        # Fallback to default full-day window if parsing fails
        default_start = datetime.strptime(f"{date_str} 10:00 AM", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
        default_end = datetime.strptime(f"{date_str} 10:00 PM", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST) + timedelta(hours=2)
        return default_start, default_end
    return min(start_times), max(end_times)


def _recompute_group_status(rig_sessions: dict) -> str:
    """A group booking (rig_ids) has no single session to be Active/Completed — each rig
    is started/ended/cancelled independently (one friend can leave early, or no-show,
    while others keep playing). This recomputes ONE aggregate status for consumers that
    still display a single status per booking (get_user_bookings_handler, the customer's
    own booking history) — Active if any rig is Active; Cancelled only once every rig is
    Cancelled (a full no-show); Completed once every rig is Completed-or-Cancelled with
    at least one actually Completed (some real play happened); otherwise Upcoming. Live
    Floor itself (list_sessions_handler below) shows the real per-rig state directly and
    doesn't need this aggregate."""
    statuses = [rs.get("status", "Upcoming") for rs in (rig_sessions or {}).values()]
    if any(s == "Active" for s in statuses):
        return "Active"
    if statuses and all(s == "Cancelled" for s in statuses):
        return "Cancelled"
    if statuses and all(s in ("Completed", "Cancelled") for s in statuses) and any(s == "Completed" for s in statuses):
        return "Completed"
    return "Upcoming"


def list_sessions_handler(cafe_id: str):
    """
    Returns all active and reserved sessions for a given cafe on the current date.

    A booking's scheduled slot end time is never, on its own, a reason to mark it
    Completed — that used to happen automatically here the instant `now` passed the
    slot's end, even for a booking the admin had never actually started (and for a
    started session, even if the customer was still playing past the scheduled end).
    Completion is admin-only now, via end_session_handler (the "End Session" button on
    Live Floor) — a booking stays visible (Reserved if never started, Occupied if
    started) until the admin explicitly ends it, no matter how much time has passed.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        # Reset non-maintenance rigs status to "available" initially so they are clean
        # before being recomputed from currently-open bookings below — this is what lets
        # a rig fall back to available once its booking is (manually) completed/cancelled.
        db_main.rigs.update_many(
            {"cafe_id": cafe_id, "status": {"$ne": "maintenance"}},
            {"$set": {"status": "available"}}
        )

        # Re-fetch current bookings & rigs (including future bookings)
        bookings = list(db_main.bookings.find({
            "cafe_id": cafe_id,
            "status": {"$in": ["Active", "Upcoming"]}
        }).sort([("date", 1), ("slots", 1)]))
        
        rigs = list(db_main.rigs.find({"cafe_id": cafe_id}))
        rig_map = {r.get("name", ""): r for r in rigs}  # name -> rig doc (old-shape bookings)
        rigs_by_id = {str(r["_id"]): r for r in rigs}   # id -> rig doc (new-shape bookings)

        mapped_sessions = []
        rigs_with_bookings = set()

        for b in bookings:
            if b.get("rig_ids"):
                # New-shape group booking — one tile PER RIG in the group, each reflecting
                # that rig's own rig_sessions sub-state, since sessions are started/ended
                # independently per rig (see create_group_booking_handler's rig_sessions
                # field and the feature plan's explicit per-rig decision).
                from .bookings_handler import _time_str_to_minutes, parse_window, _minutes_to_display
                b_id = str(b["_id"])
                b_date = b.get("date")
                is_today = (b_date == today_str)
                start_minutes = _time_str_to_minutes(b.get("start_time"))
                duration_minutes = b.get("duration_minutes") or 0
                if start_minutes is None:
                    continue
                window_start, window_end = parse_window(b_date, start_minutes, duration_minutes)
                rig_sessions = b.get("rig_sessions") or {}

                user_name_raw = b.get("user_name") or b.get("userName")
                cust_name = user_name_raw or "Guest Player"
                if cust_name.isupper() and len(cust_name.split()) == 1:
                    cust_name = cust_name.capitalize()

                for rig_id in b["rig_ids"]:
                    matched_rig = rigs_by_id.get(rig_id)
                    if not matched_rig:
                        continue
                    rs = rig_sessions.get(rig_id, {"status": "Upcoming"})
                    rs_status = rs.get("status", "Upcoming")
                    if rs_status in ("Completed", "Cancelled"):
                        continue  # this rig is free again — no tile needed
                    rig_name = matched_rig.get("name", "")
                    rigs_with_bookings.add(rig_name)

                    if rs_status == "Active":
                        if matched_rig.get("status") != "maintenance":
                            db_main.rigs.update_one({"_id": matched_rig["_id"]}, {"$set": {"status": "occupied"}})
                            matched_rig["status"] = "occupied"
                    elif is_today:
                        if matched_rig.get("status") not in ["occupied", "maintenance"]:
                            db_main.rigs.update_one({"_id": matched_rig["_id"]}, {"$set": {"status": "reserved"}})
                            matched_rig["status"] = "reserved"

                    actual_end_raw = rs.get("actual_end_at")
                    if actual_end_raw and rs_status == "Active":
                        try:
                            actual_end_dt = datetime.fromisoformat(actual_end_raw)
                            if actual_end_dt.tzinfo is None:
                                actual_end_dt = actual_end_dt.replace(tzinfo=IST)
                            synced_end = actual_end_dt
                        except Exception:
                            synced_end = window_end
                    else:
                        synced_end = window_end

                    mapped_sessions.append({
                        # Composite tile id — one booking can now produce several tiles,
                        # one per rig, so the tile's own id must disambiguate them; the
                        # real booking_id is carried separately for start/end/extend calls.
                        "id": f"{b_id}:{rig_id}",
                        "booking_id": b_id,
                        "system_id": rig_id,
                        "rig_name": rig_name,
                        "date": b_date,
                        "slots": [f"{b.get('start_time')}-{b.get('end_time')}"],
                        "customer_name": cust_name,
                        "start_at": rs.get("started_at") or window_start.isoformat(),
                        "scheduled_end_at": synced_end.isoformat(),
                        "time_label": f"{b_date} · {_minutes_to_display(start_minutes)} - {_minutes_to_display(start_minutes + duration_minutes)}",
                        "status": "active" if rs_status == "Active" else "reserved",
                    })
                continue

            # Old-shape (single rig, fixed hourly slots) booking — one session entry.
            b_rig_name = b.get("rig", "").replace("•", "·").split("·")[0].strip()
            matched_rig = rig_map.get(b_rig_name)
            if not matched_rig:
                continue

            rig_id_str = str(matched_rig["_id"])
            rigs_with_bookings.add(b_rig_name)
            b_status = b.get("status")
            slots = b.get("slots", [])
            earliest_start, latest_end = parse_slot_times(b.get("date"), slots)

            # Sync rig status to today's booking. Nothing auto-completes a booking anymore
            # (see list_sessions_handler's docstring), so an "Upcoming" booking still in
            # this list for today is either happening right now, later today, or overdue
            # and awaiting the admin's manual action — all three cases must still show the
            # rig as reserved: the Systems page and the Live Floor summary tiles both read
            # rigs.status directly, so leaving it "available" would let the owner
            # double-book or assign a walk-in onto an already-reserved PC.
            is_today = (b.get("date") == today_str)

            if b_status == "Active":
                # Active session always marks rig as occupied
                if matched_rig.get("status") != "maintenance":
                    db_main.rigs.update_one({"_id": matched_rig["_id"]}, {"$set": {"status": "occupied"}})
                    matched_rig["status"] = "occupied"
            elif b_status == "Upcoming" and is_today:
                # Any of today's upcoming bookings mark the rig as reserved, whether the
                # slot has started yet or not.
                if matched_rig.get("status") not in ["occupied", "maintenance"]:
                    db_main.rigs.update_one({"_id": matched_rig["_id"]}, {"$set": {"status": "reserved"}})
                    matched_rig["status"] = "reserved"

            # Use admin-set actual_end_at if available, else fall back to slot end
            actual_end_at_raw = b.get("actual_end_at")
            if actual_end_at_raw and b_status == "Active":
                try:
                    actual_end_dt = datetime.fromisoformat(actual_end_at_raw)
                    if actual_end_dt.tzinfo is None:
                        actual_end_dt = actual_end_dt.replace(tzinfo=IST)
                    synced_end = actual_end_dt
                except Exception:
                    synced_end = latest_end
            else:
                synced_end = latest_end

            # Retrieve customer name (first name + last name mapping)
            user_name_raw = b.get("user_name") or b.get("userName")
            email_raw = b.get("user_email", "").strip().lower()
            
            if email_raw == "harshdpatil2007@gmail.com":
                cust_name = "Harsh Patil"
            elif email_raw == "shrutidpatil0309@gmail.com":
                cust_name = "Shruti Patil"
            elif email_raw == "co2023.harsh.patil@ves.ac.in":
                cust_name = "Harsh Patil"
            elif email_raw == "vmingale2007@gmail.com":
                cust_name = "Vedant Ingale"
            elif email_raw == "pmingale5284@gmail.com":
                cust_name = "Poonam Mingale"
            else:
                cust_name = user_name_raw or "Guest Player"
                if cust_name.isupper() and len(cust_name.split()) == 1:
                    cust_name = cust_name.capitalize()

            mapped_sessions.append({
                "id": str(b["_id"]),
                "system_id": rig_id_str,
                "rig_name": b_rig_name,
                "date": b.get("date"),
                "slots": slots,
                "customer_name": cust_name,
                "start_at": b.get("started_at") or earliest_start.isoformat(),
                "scheduled_end_at": synced_end.isoformat(),
                "time_label": f"{b.get('date', today_str)} · {b.get('slot') or ', '.join(slots)}",
                "status": "active" if b_status == "Active" else "reserved"
            })

        # Free rigs that have no more bookings
        for r in rigs:
            if r.get("name") not in rigs_with_bookings and r.get("status") not in ["available", "maintenance"]:
                db_main.rigs.update_one({"_id": r["_id"]}, {"$set": {"status": "available"}})

        return {
            "status": "success",
            "sessions": mapped_sessions
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to list floor sessions: {e}"}

def start_session_handler(booking_id: "str | None" = None, data: "dict | None" = None, rig_id: "str | None" = None):
    """
    Starts a session manually.
    If booking_id is provided: Activates an online booking.
    Otherwise: Creates a new walk-in session.

    rig_id is required (and only meaningful) for a new-shape group booking (rig_ids) —
    it activates just THAT ONE rig's session within the group, independently of the
    other rigs in the same booking (one friend can start playing while another's PC
    hasn't been started yet). Ignored for an old-shape (single-rig) booking.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        if booking_id:
            booking = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
            if not booking:
                return {"status": "error", "message": "Booking not found."}

            if booking.get("rig_ids"):
                if not rig_id or rig_id not in booking["rig_ids"]:
                    return {"status": "error", "message": "A valid rig_id (from this booking's rig_ids) is required."}
                duration_minutes = booking.get("duration_minutes") or 60
                actual_end_at = now + timedelta(minutes=duration_minutes)

                rig_sessions = booking.get("rig_sessions") or {}
                rig_sessions[rig_id] = {"status": "Active", "started_at": now.isoformat(), "actual_end_at": actual_end_at.isoformat()}
                new_group_status = _recompute_group_status(rig_sessions)
                db_main.bookings.update_one(
                    {"_id": ObjectId(booking_id)},
                    {"$set": {f"rig_sessions.{rig_id}": rig_sessions[rig_id], "status": new_group_status}},
                )
                db_main.rigs.update_one({"_id": ObjectId(rig_id)}, {"$set": {"status": "occupied"}})
                return {"status": "success", "message": "Session started successfully.", "actual_end_at": actual_end_at.isoformat()}

            # 1. Activation of an old-shape (single-rig) reservation
            # Calculate actual end time from slot duration
            slots = booking.get("slots", [])
            booking_date = booking.get("date", today_str)
            try:
                # Sum individual slot durations to support non-contiguous slots correctly
                scheduled_duration = timedelta()
                for slot in slots:
                    parts = slot.split("-")
                    if len(parts) == 2:
                        try:
                            st = datetime.strptime(f"{booking_date} {parts[0].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                            et = datetime.strptime(f"{booking_date} {parts[1].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                            if et <= st:
                                et += timedelta(days=1)
                            scheduled_duration += (et - st)
                        except Exception:
                            scheduled_duration += timedelta(hours=1)
                    else:
                        scheduled_duration += timedelta(hours=1)

                if scheduled_duration.total_seconds() == 0:
                    scheduled_duration = timedelta(hours=1)

                actual_end_at = now + scheduled_duration
            except Exception:
                actual_end_at = now + timedelta(hours=1)

            db_main.bookings.update_one(
                {"_id": ObjectId(booking_id)},
                {"$set": {
                    "status": "Active",
                    "started_at": now.isoformat(),
                    "actual_end_at": actual_end_at.isoformat()
                }}
            )
            
            rig_name = booking.get("rig", "").replace("•", "·").split("·")[0].strip()
            db_main.rigs.update_one(
                {"cafe_id": booking["cafe_id"], "name": rig_name},
                {"$set": {"status": "occupied"}}
            )
            return {"status": "success", "message": "Session started successfully.", "actual_end_at": actual_end_at.isoformat()}
            
        elif data:
            # 2. Starting a manual walk-in session from scratch
            system_id = data.get("system_id") or data.get("systemId")
            customer_name = data.get("customer_name") or data.get("customerName") or "Walk-in Customer"
            hours, hours_error = input_validation.parse_bounded_number(
                data.get("duration_hours") or data.get("hours") or 1.0, "Duration", min_val=0.5, max_val=24, is_float=True
            )
            if hours_error or hours is None:
                return {"status": "error", "message": hours_error or "Duration is required."}

            rig = db_main.rigs.find_one({"_id": ObjectId(system_id)})
            if not rig:
                return {"status": "error", "message": "Hardware station not found."}
                
            cafe = db_main.cafes.find_one({"_id": ObjectId(rig["cafe_id"])})
            cafe_name = cafe.get("name", "Unknown Cafe") if cafe else "Unknown Cafe"
            
            end_time = now + timedelta(hours=hours)
            slot_str = f"{now.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}"
            price = int(rig.get("hourly_price", 80) * hours)
            code = str(random.randint(100000, 999999))
            
            booking_doc = {
                "user_email": "walkin@bookmyconsole.com",
                "user_name": customer_name,
                "cafe_id": rig["cafe_id"],
                "cafe_name": cafe_name,
                "zone": rig.get("zone", "Standard"),
                "date": today_str,
                "slots": [slot_str],
                "price": price,
                "code": code,
                "rig": rig["name"],
                "status": "Active",
                "createdAt": now
            }
            
            res = db_main.bookings.insert_one(booking_doc)
            db_main.rigs.update_one({"_id": ObjectId(system_id)}, {"$set": {"status": "occupied"}})
            
            return {
                "status": "success",
                "message": "Walk-in session started.",
                "booking_id": str(res.inserted_id)
            }
            
        return {"status": "error", "message": "Missing booking_id or walk-in data."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to start session: {e}"}

def end_session_handler(booking_id: str, rig_id: "str | None" = None):
    """
    Ends a session early, setting status to Completed and setting actual_end_at to now.
    Keeps all slots and original price/amount intact for history/payment reporting.

    rig_id ends just that ONE rig's session within a new-shape group booking,
    independently of the others (see start_session_handler's docstring) — ignored for an
    old-shape (single-rig) booking.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        now = datetime.now(IST)
        booking = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
        if not booking:
            return {"status": "error", "message": "Booking not found."}

        if booking.get("rig_ids"):
            if not rig_id or rig_id not in booking["rig_ids"]:
                return {"status": "error", "message": "A valid rig_id (from this booking's rig_ids) is required."}
            rig_sessions = booking.get("rig_sessions") or {}
            rig_sessions[rig_id] = {**rig_sessions.get(rig_id, {}), "status": "Completed", "actual_end_at": now.isoformat()}
            new_group_status = _recompute_group_status(rig_sessions)
            db_main.bookings.update_one(
                {"_id": ObjectId(booking_id)},
                {"$set": {f"rig_sessions.{rig_id}": rig_sessions[rig_id], "status": new_group_status}},
            )
            db_main.rigs.update_one({"_id": ObjectId(rig_id)}, {"$set": {"status": "available"}})

            # Ending early frees this rig for the remainder of its originally booked
            # window — but create_group_booking_handler already confirmed this rig's
            # slot_locks docs into permanent (no expires_at) records for that whole
            # window, which nothing else ever deletes. Without this, rebooking the now-
            # free remainder of the window fails with a duplicate-key conflict even
            # though get_rig_availability_handler already shows it as free. Same cleanup
            # as cancel_session_handler.
            from .bookings_handler import _micro_slot_keys, _time_str_to_minutes
            start_minutes = _time_str_to_minutes(booking.get("start_time"))
            duration_minutes = booking.get("duration_minutes")
            if start_minutes is not None and duration_minutes is not None:
                micro_keys = _micro_slot_keys(start_minutes, duration_minutes)
                db_main.slot_locks.delete_many({
                    "cafe_id": booking["cafe_id"], "rig": rig_id, "date": booking.get("date"),
                    "slot": {"$in": micro_keys},
                })
            return {"status": "success", "message": "Session ended successfully."}

        # Keep original price, amount, slots, and slot intact, only set status and actual_end_at
        db_main.bookings.update_one(
            {"_id": ObjectId(booking_id)},
            {
                "$set": {
                    "status": "Completed",
                    "actual_end_at": now.isoformat()
                }
            }
        )

        # Free the rig
        rig_name = booking.get("rig", "").split("·")[0].strip()
        db_main.rigs.update_one(
            {"cafe_id": booking["cafe_id"], "name": rig_name},
            {"$set": {"status": "available"}}
        )

        # Same permanent-lock cleanup as cancel_session_handler's old-shape branch.
        if booking.get("slots"):
            db_main.slot_locks.delete_many({
                "cafe_id": booking["cafe_id"], "rig": rig_name, "date": booking.get("date"),
                "slot": {"$in": booking.get("slots")},
            })

        return {
            "status": "success",
            "message": "Session ended successfully. Booking marked as Completed."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to end session: {e}"}


def cancel_session_handler(booking_id: str, rig_id: "str | None" = None):
    """
    Cancels a reservation that never actually started — the no-show flow: the admin
    calls the customer, they don't show, the admin cancels so the slot frees back up for
    someone else immediately (see get_rig_availability_handler, which skips a Cancelled
    rig/booking from the mobile app's busy-interval computation).

    Deliberately rejects cancelling a session that's already Active — that's what "End
    Session" is for; this is only for a reservation that was never actually started.

    rig_id cancels just that ONE rig's reservation within a new-shape group booking,
    independently of the others (see start_session_handler's docstring) — ignored for an
    old-shape (single-rig) booking.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        now = datetime.now(IST)
        booking = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
        if not booking:
            return {"status": "error", "message": "Booking not found."}

        if booking.get("rig_ids"):
            if not rig_id or rig_id not in booking["rig_ids"]:
                return {"status": "error", "message": "A valid rig_id (from this booking's rig_ids) is required."}
            rig_sessions = booking.get("rig_sessions") or {}
            current_status = rig_sessions.get(rig_id, {}).get("status", "Upcoming")
            if current_status == "Active":
                return {"status": "error", "message": "Can't cancel a session that's already started — use End Session instead."}
            rig_sessions[rig_id] = {**rig_sessions.get(rig_id, {}), "status": "Cancelled", "cancelled_at": now.isoformat()}
            new_group_status = _recompute_group_status(rig_sessions)
            db_main.bookings.update_one(
                {"_id": ObjectId(booking_id)},
                {"$set": {f"rig_sessions.{rig_id}": rig_sessions[rig_id], "status": new_group_status}},
            )
            db_main.rigs.update_one({"_id": ObjectId(rig_id)}, {"$set": {"status": "available"}})

            # create_group_booking_handler confirms this rig's slot_locks docs into
            # permanent (no expires_at/owner_token) records when the booking is first
            # created — they're never deleted on their own. Without removing them here,
            # the unique (cafe_id, rig, date, slot) index still "owns" this window
            # forever, so rebooking the exact same freed slot fails with a duplicate-key
            # conflict even though every other availability check now correctly shows it
            # as free.
            from .bookings_handler import _micro_slot_keys, _time_str_to_minutes
            start_minutes = _time_str_to_minutes(booking.get("start_time"))
            duration_minutes = booking.get("duration_minutes")
            if start_minutes is not None and duration_minutes is not None:
                micro_keys = _micro_slot_keys(start_minutes, duration_minutes)
                db_main.slot_locks.delete_many({
                    "cafe_id": booking["cafe_id"], "rig": rig_id, "date": booking.get("date"),
                    "slot": {"$in": micro_keys},
                })
            return {"status": "success", "message": "Reservation cancelled."}

        if booking.get("status") == "Active":
            return {"status": "error", "message": "Can't cancel a session that's already started — use End Session instead."}

        db_main.bookings.update_one(
            {"_id": ObjectId(booking_id)},
            {"$set": {"status": "Cancelled", "cancelled_at": now.isoformat()}}
        )

        # Free the rig
        rig_name = booking.get("rig", "").split("·")[0].strip()
        db_main.rigs.update_one(
            {"cafe_id": booking["cafe_id"], "name": rig_name},
            {"$set": {"status": "available"}}
        )

        # Same permanent-lock cleanup as the group-booking branch above — old-shape
        # bookings confirm their slot_locks docs (keyed by rig NAME + exact slot
        # strings, not rig_id + micro-slot ticks) permanently at creation time too.
        if booking.get("slots"):
            db_main.slot_locks.delete_many({
                "cafe_id": booking["cafe_id"], "rig": rig_name, "date": booking.get("date"),
                "slot": {"$in": booking.get("slots")},
            })

        return {"status": "success", "message": "Reservation cancelled."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to cancel reservation: {e}"}


def extend_session_handler(booking_id: str, minutes: int, rig_id: "str | None" = None):
    """
    Extends an active session by the specified number of minutes.

    rig_id extends just that ONE rig's session within a new-shape group booking —
    ignored for an old-shape (single-rig) booking.
    """
    db_main = get_db()
    if db_main is None:
        return {"status": "error", "message": "MongoDB connection is not established."}

    try:
        booking = db_main.bookings.find_one({"_id": ObjectId(booking_id)})
        if not booking:
            return {"status": "error", "message": "Booking not found."}

        if booking.get("rig_ids"):
            if not rig_id or rig_id not in booking["rig_ids"]:
                return {"status": "error", "message": "A valid rig_id (from this booking's rig_ids) is required."}
            rig_sessions = booking.get("rig_sessions") or {}
            rs = rig_sessions.get(rig_id, {})
            actual_end_at_raw = rs.get("actual_end_at")
            if actual_end_at_raw:
                try:
                    actual_end_dt = datetime.fromisoformat(actual_end_at_raw)
                    if actual_end_dt.tzinfo is None:
                        actual_end_dt = actual_end_dt.replace(tzinfo=IST)
                    new_actual_end = actual_end_dt + timedelta(minutes=minutes)
                except Exception:
                    new_actual_end = datetime.now(IST) + timedelta(minutes=minutes)
            else:
                new_actual_end = datetime.now(IST) + timedelta(minutes=minutes)
            rig_sessions[rig_id] = {**rs, "actual_end_at": new_actual_end.isoformat()}
            db_main.bookings.update_one(
                {"_id": ObjectId(booking_id)},
                {"$set": {f"rig_sessions.{rig_id}": rig_sessions[rig_id]}},
            )
            return {"status": "success", "message": f"Extended session by {minutes} minutes."}

        slots = booking.get("slots", [])
        if not slots:
            return {"status": "error", "message": "Cannot extend a booking with no slots."}

        # Parse the latest slot end time and add extension minutes
        earliest_start, latest_end = parse_slot_times(booking.get("date"), slots)
        new_end = latest_end + timedelta(minutes=minutes)
        
        # Modify the last slot in the array or replace it to show new extension
        last_slot = slots[-1]
        parts = last_slot.split("-")
        if len(parts) == 2:
            new_last_slot = f"{parts[0].strip()} - {new_end.strftime('%I:%M %p')}"
            slots[-1] = new_last_slot

        # Also extend actual_end_at if set
        actual_end_at_raw = booking.get("actual_end_at")
        if actual_end_at_raw:
            try:
                actual_end_dt = datetime.fromisoformat(actual_end_at_raw)
                if actual_end_dt.tzinfo is None:
                    actual_end_dt = actual_end_dt.replace(tzinfo=IST)
                new_actual_end = actual_end_dt + timedelta(minutes=minutes)
            except Exception:
                new_actual_end = new_end
        else:
            new_actual_end = new_end

        # Save extended slots, slot string, and actual_end_at
        db_main.bookings.update_one(
            {"_id": ObjectId(booking_id)},
            {"$set": {
                "slots": slots,
                "slot": ", ".join(slots),
                "actual_end_at": new_actual_end.isoformat()
            }}
        )

        return {
            "status": "success",
            "message": f"Extended session by {minutes} minutes."
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to extend session: {e}"}

