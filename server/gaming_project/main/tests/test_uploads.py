# test_uploads.py
# Regression tests for the Cloudinary upload file-type/size validation.

from django.test import SimpleTestCase

from ..Handlers.upload_validation import validate_image_upload


class FakeUploadedFile:
    def __init__(self, content_type, size):
        self.content_type = content_type
        self.size = size


class ImageUploadValidationTests(SimpleTestCase):
    def test_no_file_is_allowed(self):
        self.assertIsNone(validate_image_upload(None))

    def test_valid_png_is_allowed(self):
        self.assertIsNone(validate_image_upload(FakeUploadedFile("image/png", 1024)))

    def test_valid_jpeg_is_allowed(self):
        self.assertIsNone(validate_image_upload(FakeUploadedFile("image/jpeg", 1024)))

    def test_svg_is_rejected(self):
        """SVG can embed <script> — a well-known stored-XSS vector — so it's excluded
        even though it's technically an image format."""
        self.assertIsNotNone(validate_image_upload(FakeUploadedFile("image/svg+xml", 1024)))

    def test_non_image_disguised_upload_is_rejected(self):
        self.assertIsNotNone(validate_image_upload(FakeUploadedFile("text/html", 1024)))
        self.assertIsNotNone(validate_image_upload(FakeUploadedFile("application/javascript", 1024)))

    def test_oversized_file_is_rejected(self):
        ten_mb = 10 * 1024 * 1024
        self.assertIsNotNone(validate_image_upload(FakeUploadedFile("image/png", ten_mb)))

    def test_file_at_size_limit_is_allowed(self):
        five_mb = 5 * 1024 * 1024
        self.assertIsNone(validate_image_upload(FakeUploadedFile("image/png", five_mb)))
