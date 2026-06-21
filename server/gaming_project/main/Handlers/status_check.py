# status_check.py
# Simple server health check


def status_check():
    """Returns a simple status OK response."""
    return {
        "status": "ok",
        "message": "KheloMore Gaming Hub API is running.",
        "version": "1.0.0"
    }
