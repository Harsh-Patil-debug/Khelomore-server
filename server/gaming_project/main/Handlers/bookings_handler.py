import random
from datetime import datetime, timezone, timedelta
from bson.objectid import ObjectId
from pymongo.errors import DuplicateKeyError, OperationFailure
from .db_connection import db_main, get_client
from .email_handler import send_booking_confirmation_email, send_booking_admin_notification_email

IST = timezone(timedelta(hours=5, minutes=30))

_slot_locks_index_ensured = False


def _ensure_slot_locks_index():
    """Lazily creates the unique index backing atomic slot claiming (see
    create_booking_handler). One document per (cafe_id, rig, date, slot) — the unique
    index is what makes a double-booking attempt fail at the database level instead of
    racing on an application-level read-then-write check, which has always had a gap
    between the availability read and the booking insert."""
    global _slot_locks_index_ensured
    if not _slot_locks_index_ensured:
        try:
            db_main.slot_locks.create_index(
                [("cafe_id", 1), ("rig", 1), ("date", 1), ("slot", 1)], unique=True
            )
            # TTL cleanup for abandoned pre-payment holds (see hold_slots_for_payment) —
            # a confirmed booking's lock doc never has expires_at set, so this can never
            # touch a real booking, only an unpaid/abandoned hold.
            db_main.slot_locks.create_index("expires_at", expireAfterSeconds=0)
        except Exception:
            pass
        _slot_locks_index_ensured = True

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
        default_start = datetime.strptime(f"{date_str} 10:00 AM", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
        default_end = datetime.strptime(f"{date_str} 10:00 PM", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST) + timedelta(hours=2)
        return default_start, default_end
    return min(start_times), max(end_times)
def calculate_booking_status_and_time(date_str: str, slots: list, db_status: str = "Upcoming", actual_end_at=None) -> tuple:
    """
    SECURITY/UX: completion is admin-only, via end_session_handler — this must never
    derive "Completed" purely from the slot's scheduled end time having passed for a
    same-day booking. It used to, which raced against Live Floor (which shows the real
    db_status) — a booking would flip to "Completed" here the instant the clock passed
    its slot end even though the admin never actually ended it, while Live Floor kept
    showing it Reserved/Active until it was manually ended. A booking from an earlier
    calendar day is unambiguous either way, so that one case still auto-resolves.
    """
    # 0. If the database already has a final status (Completed/Cancelled), respect it!
    if db_status in ["Completed", "Cancelled", "completed", "cancelled"]:
        return "Completed" if db_status.lower() == "completed" else "Cancelled", 0

    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")

    # 1. If date is in the past, it's completed
    if date_str < today_str:
        return "Completed", 0

    # 2. Parse slot times to find latest end time
    try:
        earliest_start, latest_end = parse_slot_times(date_str, slots)

        # 3. If booking has been started manually by admin (status is "Active")
        if db_status == "Active":
            # Use actual_end_at from DB if set (synced timer), else fall back to slot end.
            # Elapsed time only clamps the displayed remaining time to 0 — it never flips
            # the status away from Active on its own.
            end_time = actual_end_at if actual_end_at else latest_end
            remaining_seconds = int((end_time - now).total_seconds())
            return "Active", max(0, remaining_seconds)

        # 4. Otherwise it stays Upcoming/Reserved — whether the slot hasn't started yet,
        # is happening now, or is overdue and awaiting the admin's manual action.
        return "Upcoming", 0

    except Exception as e:
        print(f"Error parsing slot times: {str(e)}")
        return db_status, 0

def get_booked_slots_handler(cafe_id: str, zone: str, date: str):
    """
    Returns a list of all booked slot strings for a given cafe, zone, and date.
    """
    try:
        bookings = db_main.bookings.find({
            "cafe_id": cafe_id,
            "zone": zone,
            "date": date,
            "status": {"$in": ["Upcoming", "Active"]}
        })
        
        booked_slots = []
        for b in bookings:
            slots_list = b.get("slots", [])
            if isinstance(slots_list, list):
                booked_slots.extend(slots_list)
            elif isinstance(slots_list, str):
                booked_slots.extend([s.strip() for s in slots_list.split(",") if s.strip()])
                
        return {
            "status": "success",
            "booked_slots": list(set(booked_slots))
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve booked slots: {str(e)}"
        }, 500

def check_slot_conflict(cafe_id: str, date: str, zone: "str | None", slots: list, rig: "str | None" = None):
    """
    Read-only conflict check shared by two call sites: the pre-payment check in
    create_cafe_booking_order_handler (payments.py), so a user is told a slot is taken
    BEFORE paying rather than after, and step 1 of create_booking_handler below, which
    re-checks post-payment since this read can still race (see the atomic slot_locks
    claim later in create_booking_handler, which is the actual source of truth).
    Returns (conflict_message_or_None,).
    """
    existing_bookings = list(db_main.bookings.find({
        "cafe_id": cafe_id,
        "date": date,
        "status": {"$in": ["Upcoming", "Active"]}
    }))

    if rig:
        clean_req_rig = rig.replace("•", "·").replace("  ", " ").split("·")[0].strip()
        for b in existing_bookings:
            b_rig = b.get("rig", "").replace("•", "·").replace("  ", " ").split("·")[0].strip()
            if b_rig == clean_req_rig:
                b_slots = b.get("slots", [])
                overlapping = [s for s in slots if s in b_slots]
                if overlapping:
                    return f"Conflict detected: Station '{clean_req_rig}' is already booked for slots {overlapping}."
        return None

    # Fallback to zone-wide capacity validation if no specific rig is selected
    rigs = list(db_main.rigs.find({"cafe_id": cafe_id}))
    if zone == "Console Lounge":
        matching_rigs = [r for r in rigs if r.get("type", "").upper() in ["PS5", "XBOX"]]
    else:
        matching_rigs = [r for r in rigs if r.get("type", "").upper() == "PC"]

    matching_rig_names = {r.get("name") for r in matching_rigs}

    for slot in slots:
        bookings_for_slot = 0
        for b in existing_bookings:
            b_rig = b.get("rig", "").replace("•", "·").replace("  ", " ").split("·")[0].strip()
            if b_rig in matching_rig_names and slot in b.get("slots", []):
                bookings_for_slot += 1
        if bookings_for_slot >= len(matching_rigs) and len(matching_rigs) > 0:
            return f"Conflict detected: All stations in {zone} are fully booked for slot '{slot}'."
    return None


def hold_slots_for_payment(cafe_id: str, date: str, zone: "str | None", slots: list, rig: "str | None", hold_token: str, hold_minutes: int = 10):
    """
    Atomically reserves (cafe_id, rig, date, slot) for each requested slot BEFORE payment
    starts, using the same slot_locks unique index create_booking_handler's final claim
    uses. This is what actually closes the "two people both pass the read-only
    availability check, both pay, only one wins" race — instead of checking then hoping,
    the slot is claimed the instant checkout begins, so the loser is rejected before
    paying, not after. expires_at (TTL-indexed) means an abandoned checkout — closed,
    timed out, app killed — releases the slot on its own without needing an explicit
    release call; release_slot_hold below is just the fast path for a clean cancel.
    owner_token (the caller's Razorpay order id) proves a later confirm/release call
    actually owns this specific hold, not merely that a lock document happens to exist.
    Returns (ok: bool, message: str | None).
    """
    if not rig:
        # Auto-assign/zone-capacity bookings have no single lock key to hold atomically
        # (capacity is a count threshold, not one key) — fall back to the read-only
        # check. The mobile customer payment flow always supplies a specific rig, so this
        # path is only hit by callers outside that flow.
        message = check_slot_conflict(cafe_id, date, zone, slots, rig)
        return (message is None), message

    _ensure_slot_locks_index()
    clean_rig_key = rig.split("·")[0].strip()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=hold_minutes)
    lock_docs = [
        {"cafe_id": cafe_id, "rig": clean_rig_key, "date": date, "slot": s,
         "expires_at": expires_at, "owner_token": hold_token}
        for s in slots
    ]
    try:
        db_main.slot_locks.insert_many(lock_docs, ordered=True)
        return True, None
    except DuplicateKeyError:
        return False, ("That station/slot is already booked or is currently being paid "
                        "for by someone else. Please pick a different slot.")
    except Exception as e:
        return False, f"Failed to hold slot: {e}"


def release_slot_hold(cafe_id: str, date: str, rig: "str | None", slots: list, hold_token: "str | None"):
    """Releases an unconfirmed hold immediately (checkout was cancelled/failed) instead of
    waiting out the TTL. Matches on owner_token, so this can only ever delete a hold this
    exact checkout created — never a confirmed booking (which has no owner_token once
    create_booking_handler confirms it) or a different, still-valid hold."""
    if not rig or not hold_token:
        return
    clean_rig_key = rig.split("·")[0].strip()
    try:
        db_main.slot_locks.delete_many({
            "cafe_id": cafe_id, "rig": clean_rig_key, "date": date,
            "slot": {"$in": slots}, "owner_token": hold_token,
        })
    except Exception:
        pass


def _resolve_hourly_price(cafe_id, rig_name=None):
    """Looks up the authoritative hourly price for a booking from the DB — never trust a client-supplied price."""
    if rig_name:
        db_rig = db_main.rigs.find_one({"cafe_id": cafe_id, "name": rig_name})
        if db_rig and db_rig.get("hourly_price") is not None:
            return db_rig.get("hourly_price")
    if cafe_id and ObjectId.is_valid(cafe_id):
        cafe = db_main.cafes.find_one({"_id": ObjectId(cafe_id)})
        if cafe and cafe.get("price_per_hour") is not None:
            return cafe.get("price_per_hour")
    return 150


def create_booking_handler(user_email: str, cafe_id: str, cafe_name: str, zone: str, date: str, slots: list, rig: str = None, user_name: str = None, user_phone: str = "", razorpay_order_id: str = None, razorpay_payment_id: str = None, razorpay_signature: str = None):
    """
    Validates slot availability and saves the booking record.
    Price is always computed server-side from the cafe/rig's hourly rate — a client-supplied
    price is never trusted. A non-zero price requires a verified Razorpay payment before the
    booking is created and marked paid.
    """
    try:
        # SECURITY: cafe_id/date come straight from the JSON request body and flow directly
        # into MongoDB query filters below — a client sending a dict here (e.g. {"$ne": "x"})
        # instead of a plain string would corrupt the availability/conflict queries against
        # ALL cafes/dates rather than the intended one. Coerce to str at the boundary rather
        # than trusting the caller's type.
        cafe_id = str(cafe_id) if cafe_id is not None else cafe_id
        date = str(date) if date is not None else date

        if not user_name:
            user_doc = db_main.users.find_one({"email": user_email})
            if user_doc:
                user_name = user_doc.get("full_name") or user_doc.get("name")
            if not user_name:
                user_name = user_email.split("@")[0].upper()
        if user_email == "harshdpatil2007@gmail.com" and (not user_name or user_name.upper() == "HARSHDPATIL2007"):
            user_name = "Harsh Patil"
        if not slots:
            return {"status": "error", "message": "No slots selected"}, 400

        # 1. Validate availability at the specific machine level
        conflict_message = check_slot_conflict(cafe_id, date, zone, slots, rig)
        if conflict_message:
            return {"status": "error", "message": conflict_message}, 400

        # 2. Determine rig name: Use client's selected rig if provided
        if rig:
            # Clean and normalize bullet symbol to center dot
            rig = rig.replace("•", "·").replace("  ", " ").strip()
            rig_name = rig.split("·")[0].strip()
            db_rig = db_main.rigs.find_one({"cafe_id": cafe_id, "name": rig_name})
            # SECURITY/INTEGRITY: a `rig` that doesn't match any real station for this
            # cafe used to be accepted as-is (silently stored verbatim, whatever the
            # client sent) — only a MATCHING rig ever got checked for anything at all.
            # Reject instead: a station either exists and is bookable, or it doesn't.
            if not db_rig:
                return {
                    "status": "error",
                    "message": f"Station '{rig_name}' doesn't exist at this cafe."
                }, 400
            if db_rig.get("status") == "maintenance":
                return {
                    "status": "error",
                    "message": f"Rig '{rig_name}' is currently under maintenance and cannot be booked."
                }, 400
            # Rebuild `rig` from the DB's own fields rather than trusting the client's
            # exact string — the name/spec that ends up stored and emailed is always
            # exactly what's in the rigs collection, never client-supplied text.
            db_rig_spec = db_rig.get("spec", "")
            rig = f"{db_rig.get('name')} · {db_rig_spec}" if db_rig_spec else db_rig.get("name")
        else:
            # Rig auto-assignment
            rigs = list(db_main.rigs.find({"cafe_id": cafe_id}))
            
            # Filter rigs by type based on zone, ignoring any under maintenance
            if zone == "Console Lounge":
                matching_rigs = [r for r in rigs if r.get("type", "").upper() in ["PS5", "XBOX"] and r.get("status") != "maintenance"]
            else:
                matching_rigs = [r for r in rigs if r.get("type", "").upper() == "PC" and r.get("status") != "maintenance"]

            if matching_rigs:
                booked_rigs = set()
                existing_bookings = db_main.bookings.find({
                    "cafe_id": cafe_id,
                    "date": date,
                    "status": {"$in": ["Upcoming", "Active"]}
                })
                for eb in existing_bookings:
                    eb_slots = eb.get("slots", [])
                    has_overlap = any(s in eb_slots for s in slots)
                    if has_overlap:
                        eb_rig = eb.get("rig", "").split("·")[0].strip()
                        booked_rigs.add(eb_rig)
                
                # Find an available rig
                available_rigs = [r for r in matching_rigs if r.get("name") not in booked_rigs]
                if available_rigs:
                    assigned_rig = random.choice(available_rigs)
                else:
                    assigned_rig = random.choice(matching_rigs)
                    
                assigned_rig_name = assigned_rig.get("name")
                rig_spec = assigned_rig.get("spec", "")
                rig = f"{assigned_rig_name} · {rig_spec}" if rig_spec else assigned_rig_name
            else:
                # Fallback to random if no rigs in database
                rig_num = random.randint(1, 7)
                if zone == "Console Lounge":
                    rig = f"PS5 #{str(rig_num).zfill(2)}"
                else:
                    rig_spec = "RTX 4090" if zone == "VIP Elite Zone" else "RTX 4070"
                    rig = f"PC #{str(rig_num).zfill(2)} · {rig_spec}"

        # 2b. Compute the authoritative price server-side and verify payment before booking.
        rig_name_for_price = rig.split("·")[0].strip() if rig else None
        hourly_price = _resolve_hourly_price(cafe_id, rig_name_for_price)
        total_price = int(hourly_price * len(slots))

        payment_settlement = None
        hold_token = None
        if total_price > 0:
            from .payments import verify_razorpay_payment
            if not verify_razorpay_payment(razorpay_order_id, razorpay_payment_id, razorpay_signature, total_price * 100, cafe_id=cafe_id):
                return {
                    "status": "error",
                    "message": "Payment verification failed. Please complete payment before booking."
                }, 402
            payment_status = "paid"
            # Record which account the money actually landed in, so a booking paid via the
            # platform fallback (cafe hadn't connected their own Razorpay yet) can be found
            # and settled to the owner manually later.
            order_record = db_main.cafe_payment_orders.find_one({"order_id": razorpay_order_id, "cafe_id": cafe_id})
            payment_settlement = "platform_pending_payout" if (order_record and order_record.get("used_platform_fallback")) else "direct_to_cafe"
            # If order creation held these exact slots (see create_cafe_booking_order_handler
            # / hold_slots_for_payment), step 4 below confirms that hold instead of claiming
            # fresh — this is what makes the payment step's atomic claim into the final,
            # decisive one, not just another read-then-hope check.
            hold_token = order_record.get("hold_token") if order_record else None
        else:
            payment_status = "paid"  # free slot, nothing owed

        # 3. Group slots into contiguous blocks and save each block as a separate booking document
        parsed_slots = []
        for s in slots:
            parts = s.split("-")
            if len(parts) == 2:
                try:
                    st = datetime.strptime(f"{date} {parts[0].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                    et = datetime.strptime(f"{date} {parts[1].strip()}", "%Y-%m-%d %I:%M %p").replace(tzinfo=IST)
                    if et <= st:
                        et += timedelta(days=1)
                    parsed_slots.append((st, et, s))
                except Exception:
                    pass
        parsed_slots.sort(key=lambda x: x[0])

        slot_groups = []
        if parsed_slots:
            current_group = [parsed_slots[0]]
            for next_slot in parsed_slots[1:]:
                last_slot = current_group[-1]
                if next_slot[0] == last_slot[1]:
                    current_group.append(next_slot)
                else:
                    slot_groups.append(current_group)
                    current_group = [next_slot]
            slot_groups.append(current_group)
        else:
            slot_groups = [[(None, None, s) for s in slots]]

        inserted_bookings = []
        for group in slot_groups:
            group_slots = [item[2] for item in group]
            group_price = int(total_price * len(group_slots) / len(slots))
            group_code = str(random.randint(100000, 999999))
            
            group_status, group_remaining = calculate_booking_status_and_time(date, group_slots)
            
            doc = {
                "user_email": user_email,
                "user_name": user_name,
                "user_phone": user_phone,
                "cafe_id": cafe_id,
                "cafe_name": cafe_name,
                "zone": zone,
                "date": date,
                "slots": group_slots,
                "slot": ", ".join(group_slots),
                "price": group_price,
                "code": group_code,
                "rig": rig,
                "status": group_status,
                "payment_status": payment_status,
                "payment_settlement": payment_settlement,
                "createdAt": datetime.now(IST)
            }
            if group_status == "Active" and group_remaining > 0:
                doc["remainingTimeSeconds"] = group_remaining
                doc["started_at"] = datetime.now(IST).isoformat()
                doc["actual_end_at"] = (datetime.now(IST) + timedelta(seconds=group_remaining)).isoformat()

            inserted_bookings.append(doc)

        # 4. Atomically claim every slot before persisting the booking. If order creation
        # already put a hold on these exact (rig, date, slot) keys (see
        # hold_slots_for_payment), confirm that hold — it was claimed the instant checkout
        # began, which is the real fix for two concurrent requests both passing an
        # availability read and both paying. Any slot without a matching owned hold (a
        # free booking, or a hold that expired during a slow checkout) falls back to
        # claiming fresh here, same as before. A unique index (cafe_id, rig, date, slot)
        # makes a losing claim fail at the database level rather than silently
        # double-booking; wrapping every claim/confirm and the booking insert in one
        # transaction means either all of it succeeds or none of it does.
        _ensure_slot_locks_index()
        clean_rig_key = rig.split("·")[0].strip() if rig else ""

        client = get_client()
        if client is None:
            return {"status": "error", "message": "Database connection unavailable."}, 500

        try:
            with client.start_session() as session:
                with session.start_transaction():
                    for s in slots:
                        lock_key = {"cafe_id": cafe_id, "rig": clean_rig_key, "date": date, "slot": s}
                        confirmed = None
                        if hold_token:
                            confirmed = db_main.slot_locks.find_one_and_update(
                                {**lock_key, "owner_token": hold_token},
                                {"$unset": {"expires_at": "", "owner_token": ""}},
                                session=session,
                            )
                        if not confirmed:
                            # Raises DuplicateKeyError if someone else's confirmed booking
                            # or still-valid hold already owns this exact key.
                            db_main.slot_locks.insert_one(lock_key, session=session)
                    if len(inserted_bookings) > 1:
                        db_main.bookings.insert_many(inserted_bookings, session=session)
                    else:
                        result = db_main.bookings.insert_one(inserted_bookings[0], session=session)
                        inserted_bookings[0]["_id"] = result.inserted_id
        except (DuplicateKeyError, OperationFailure) as e:
            # Inside a transaction, a duplicate-key write error on slot_locks doesn't
            # surface as a bare DuplicateKeyError — pymongo wraps it in OperationFailure/
            # BulkWriteError, with code 11000 nested in the error's details. Check for
            # that specifically rather than treating every OperationFailure as the same
            # "slot taken" case (a real transaction/infra failure should stay a 500, not
            # be mislabeled as a conflict).
            is_conflict = getattr(e, "code", None) == 11000 or "E11000" in str(e)
            if is_conflict:
                # Someone else claimed one of these exact (rig, date, slot) combinations
                # first — the transaction was rolled back entirely, nothing was written.
                # Payment was already captured via Razorpay before this call (the client
                # completes checkout before calling this endpoint), so a race loss here
                # means the payment needs a manual refund — a real residual gap, not
                # something this fix resolves, since automated refunds are a separate
                # feature. Flagged clearly in the error message.
                return {
                    "status": "error",
                    "message": (
                        "That station/slot was just booked by someone else. Your payment was "
                        "captured — contact support for a refund, and please try a different slot."
                    ),
                }, 409
            # Transactions require a replica set (true for MongoDB Atlas, which this
            # project uses) — if that's ever not the case, fail loudly rather than
            # silently falling back to the non-atomic path this fix exists to remove.
            return {"status": "error", "message": f"Booking transaction failed: {e}"}, 500

        for doc in inserted_bookings:
            doc["id"] = str(doc["_id"])
            del doc["_id"]
            if isinstance(doc.get("createdAt"), datetime):
                doc["createdAt"] = doc["createdAt"].isoformat()

        # Confirmation emails — never let a Brevo hiccup fail a booking that already
        # succeeded in the DB. Summed across every slot-group so the user/cafe get one
        # email covering the whole booking, not one per contiguous slot block.
        try:
            primary = inserted_bookings[0]
            full_slot_str = ", ".join(sorted({s for b in inserted_bookings for s in b.get("slots", [])}))
            total_booking_price = sum(b.get("price", 0) for b in inserted_bookings)
            send_booking_confirmation_email(
                recipient=user_email, user_name=user_name, cafe_name=cafe_name,
                rig=primary.get("rig"), zone=zone, date=date, slot=full_slot_str,
                price=total_booking_price, code=primary.get("code"),
            )
            cafe_owner_email = None
            if cafe_id and ObjectId.is_valid(cafe_id):
                cafe_doc = db_main.cafes.find_one({"_id": ObjectId(cafe_id)}, {"owner_email": 1})
                cafe_owner_email = cafe_doc.get("owner_email") if cafe_doc else None
            if cafe_owner_email:
                send_booking_admin_notification_email(
                    recipient=cafe_owner_email, user_name=user_name, user_phone=user_phone,
                    cafe_name=cafe_name, rig=primary.get("rig"), zone=zone, date=date,
                    slot=full_slot_str, price=total_booking_price, code=primary.get("code"),
                )
        except Exception as email_err:
            print(f"[BookMyConsole] Booking confirmation email failed (booking still saved): {email_err}")

        return {
            "status": "success",
            "message": "Booking secured successfully",
            "booking": inserted_bookings[0]
        }, 201

    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to create booking: {str(e)}"
        }, 500

def get_user_bookings_handler(user_email: str, cafe_id: str = None, date: str = None):
    """
    Fetches all bookings. If cafe_id is provided, fetches all bookings for that cafe.
    Otherwise, if the user is a Cafe Owner/Admin, fetches all bookings for their cafes.
    Otherwise, fetches all bookings made by this user email.
    """
    try:
        query = {}
        if date:
            query["date"] = date

        if cafe_id:
            query["cafe_id"] = cafe_id
            bookings = db_main.bookings.find(query).sort("createdAt", -1)
        else:
            # Check if user is a super admin
            is_super_admin = False
            if user_email == "super_admin_static":
                is_super_admin = True
            else:
                # 1. Check in super_admin collection
                super_admin_doc = db_main.super_admin.find_one({"email": user_email})
                if super_admin_doc and super_admin_doc.get("status") == "Active":
                    is_super_admin = True
                else:
                    # 2. Fallback to users role
                    user_doc = db_main.users.find_one({"email": user_email})
                    if user_doc:
                        is_super_admin = user_doc.get("is_super_admin") or user_doc.get("role") == "super_admin"
                
            if is_super_admin:
                # Super Admin sees all bookings in the system
                bookings = db_main.bookings.find(query).sort("createdAt", -1)
            else:
                # Check if this user is a cafe owner (has registered cafes)
                owned_cafes = list(db_main.cafes.find({"owner_email": user_email}))
                if owned_cafes:
                    # It's an admin! Get all bookings for their cafes OR bookings they personally made
                    cafe_ids = [str(c["_id"]) for c in owned_cafes]
                    if date:
                        query["$or"] = [
                            {"user_email": user_email},
                            {"cafe_id": {"$in": cafe_ids}}
                        ]
                        bookings = db_main.bookings.find(query).sort("createdAt", -1)
                    else:
                        bookings = db_main.bookings.find({
                            "$or": [
                                {"user_email": user_email},
                                {"cafe_id": {"$in": cafe_ids}}
                            ]
                        }).sort("createdAt", -1)
                else:
                    # It's a regular user! Get bookings they made
                    query["user_email"] = user_email
                    bookings = db_main.bookings.find(query).sort("createdAt", -1)
        
        bookings_list = []
        for b in bookings:
            b_id = str(b["_id"])
            slots = b.get("slots", [])
            del b["_id"]
            
            if "createdAt" in b:
                if isinstance(b["createdAt"], datetime):
                    b["createdAt"] = b["createdAt"].isoformat()
                else:
                    b["createdAt"] = str(b["createdAt"])
            
            slot_str = ", ".join(slots)
            
            # Recalculate status dynamically based on current time and DB state
            actual_end_at_raw = b.get("actual_end_at")
            actual_end_at_dt = None
            if actual_end_at_raw:
                try:
                    if isinstance(actual_end_at_raw, str):
                        actual_end_at_dt = datetime.fromisoformat(actual_end_at_raw)
                        if actual_end_at_dt.tzinfo is None:
                            actual_end_at_dt = actual_end_at_dt.replace(tzinfo=IST)
                    else:
                        actual_end_at_dt = actual_end_at_raw
                        if getattr(actual_end_at_dt, 'tzinfo', None) is None:
                            actual_end_at_dt = actual_end_at_dt.replace(tzinfo=IST)
                except Exception:
                    actual_end_at_dt = None

            status, remaining_time = calculate_booking_status_and_time(
                b.get("date"), slots, db_status=b.get("status", "Upcoming"), actual_end_at=actual_end_at_dt
            )
            
            item = {
                "id": b_id,
                "cafeId": b.get("cafe_id"),
                "cafeName": b.get("cafe_name"),
                "zone": b.get("zone"),
                "date": b.get("date"),
                "slot": slot_str,
                "price": b.get("price"),
                "code": b.get("code"),
                "status": status,
                "rig": b.get("rig"),
                "userEmail": b.get("user_email"),
                "userName": b.get("user_name"),
                "userPhone": b.get("user_phone") or b.get("userPhone") or "",
                "paymentStatus": b.get("payment_status") or b.get("paymentStatus") or "paid",
                "createdAt": b.get("createdAt"),
            }
            if status == "Active":
                item["remainingTimeSeconds"] = remaining_time
                if actual_end_at_raw:
                    item["actualEndAt"] = actual_end_at_raw
                if b.get("started_at"):
                    item["startedAt"] = b.get("started_at")
                
            bookings_list.append(item)
            
        return {
            "status": "success",
            "bookings": bookings_list
        }, 200
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to retrieve user bookings: {str(e)}"
        }, 500
