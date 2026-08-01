# Copyright 2026 Amazon.com, Inc. or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for file_upload_service."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from application.services.file_upload_service import (  # noqa: E402
    FileUploadServiceError,
    sanitize_image_filename,
    upload_chat_image,
)


class SanitizeImageFilenameTests(unittest.TestCase):
    def test_accepts_png_and_makes_unique(self) -> None:
        name = sanitize_image_filename("photo.png")
        self.assertTrue(name.endswith(".png"))
        self.assertIn("photo_", name)

    def test_rejects_unsupported_extension(self) -> None:
        with self.assertRaises(FileUploadServiceError) as ctx:
            sanitize_image_filename("notes.txt")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_rejects_empty_name(self) -> None:
        with self.assertRaises(FileUploadServiceError) as ctx:
            sanitize_image_filename("   ")
        self.assertEqual(ctx.exception.status_code, 400)


class UploadChatImageTests(unittest.TestCase):
    def test_empty_bytes_rejected(self) -> None:
        with self.assertRaises(FileUploadServiceError) as ctx:
            upload_chat_image(b"", "a.png")
        self.assertEqual(ctx.exception.status_code, 400)

    @patch("application.services.file_upload_service.utils.upload_to_s3")
    def test_upload_failure_mapped(self, mock_upload) -> None:
        mock_upload.side_effect = RuntimeError("s3 down")
        with self.assertRaises(FileUploadServiceError) as ctx:
            upload_chat_image(b"abc", "a.png")
        self.assertEqual(ctx.exception.status_code, 500)

    @patch("application.services.file_upload_service.utils.upload_to_s3")
    def test_success_returns_upload_fields(self, mock_upload) -> None:
        mock_upload.return_value = {
            "url": "https://example.com/images/a.png",
            "s3_key": "images/a.png",
            "file_name": "a.png",
        }
        result = upload_chat_image(b"abc", "a.png")
        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "https://example.com/images/a.png")


if __name__ == "__main__":
    unittest.main()
