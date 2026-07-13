# upload_validation.py
# Shared validation for user-uploaded image files before they're forwarded to Cloudinary.

ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


def validate_image_upload(uploaded_file):
    """
    Returns None if the file is an acceptable image upload, otherwise an error message.

    SVG is deliberately excluded from the allow-list — it can embed <script> tags and is a
    well-known stored-XSS vector whenever it's later served back and rendered inline.
    Content-Type is client-supplied and not proof of real file content, but combined with
    Cloudinary's own server-side validation on ingest it closes the easy/accidental cases.
    """
    if uploaded_file is None:
        return None

    content_type = getattr(uploaded_file, "content_type", None)
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        return f"Unsupported file type '{content_type}'. Allowed: JPEG, PNG, WEBP, GIF."

    size = getattr(uploaded_file, "size", None)
    if size is not None and size > MAX_IMAGE_UPLOAD_BYTES:
        return f"File too large ({size} bytes). Maximum allowed is {MAX_IMAGE_UPLOAD_BYTES} bytes."

    return None
