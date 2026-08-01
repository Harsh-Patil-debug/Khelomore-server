# scheduler.py
# Lightweight in-process background job runner for time-based tasks that don't need a
# full Celery/Redis stack. render.yaml runs a single gunicorn worker (no -w flag), so an
# in-process APScheduler thread is enough for a periodic per-minute check against the
# same MongoDB the request handlers already use — no risk of the same job double-firing
# across workers, since there's only ever one.

from apscheduler.schedulers.background import BackgroundScheduler

_scheduler = None


def start_scheduler():
    """Starts the background scheduler once per process. Safe to call more than once -
    AppConfig.ready() firing twice (or any other accidental re-entry) just no-ops."""
    global _scheduler
    if _scheduler is not None:
        return

    from .notifications import send_due_session_end_notifications

    def _tick():
        try:
            send_due_session_end_notifications()
        except Exception as e:
            print(f"[Scheduler] send_due_session_end_notifications tick failed: {e}")

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _tick,
        "interval",
        seconds=60,
        id="session_end_notifications",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    print("[Scheduler] Background scheduler started (session-end notifications every 60s).")
