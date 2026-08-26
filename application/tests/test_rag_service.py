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

"""Unit tests for rag_service.ingest_rag_upload."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from application.services.rag_service import RagServiceError, ingest_rag_upload  # noqa: E402


class IngestRagUploadTests(unittest.TestCase):
    @patch("application.services.rag_service.utils.get_active_ingestion_job")
    def test_active_job_returns_conflict(self, mock_active) -> None:
        mock_active.return_value = {"ingestionJobId": "job-1"}
        with self.assertRaises(RagServiceError) as ctx:
            ingest_rag_upload(b"data", "doc.pdf", "user-1")
        self.assertEqual(ctx.exception.status_code, 409)

    @patch("application.services.rag_service.utils.get_active_ingestion_job")
    def test_status_check_failure(self, mock_active) -> None:
        mock_active.side_effect = RuntimeError("kb unavailable")
        with self.assertRaises(RagServiceError) as ctx:
            ingest_rag_upload(b"data", "doc.pdf", "user-1")
        self.assertEqual(ctx.exception.status_code, 503)

    @patch("application.services.rag_service.utils.sync_data_source")
    @patch("application.services.rag_service.utils.upload_to_s3")
    @patch("application.services.rag_service.utils.get_active_ingestion_job")
    def test_happy_path(self, mock_active, mock_upload, mock_sync) -> None:
        mock_active.return_value = None
        mock_upload.return_value = {
            "file_name": "doc.pdf",
            "s3_key": "docs/user-1/doc.pdf",
            "url": "https://example.com/docs/user-1/doc.pdf",
        }
        mock_sync.return_value = {"ingestion_job_id": "job-9"}
        result = ingest_rag_upload(b"data", "doc.pdf", "user-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["sync"]["ingestion_job_id"], "job-9")

    @patch("application.services.rag_service.utils.upload_to_s3")
    @patch("application.services.rag_service.utils.get_active_ingestion_job")
    def test_upload_failure(self, mock_active, mock_upload) -> None:
        mock_active.return_value = None
        mock_upload.return_value = None
        with self.assertRaises(RagServiceError) as ctx:
            ingest_rag_upload(b"data", "doc.pdf", "user-1")
        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
