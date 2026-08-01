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

import logging
import os

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from application.api.routes_auth import require_user_id
from application.services.rag_service import RagServiceError, ingest_rag_upload

logger = logging.getLogger("routes_rag")

router = APIRouter(prefix="/api/rag", tags=["rag"])

RAG_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".html",
    ".htm",
    ".json",
    ".py",
    ".js",
}


def _validate_filename(filename: str) -> str:
    name = os.path.basename(filename or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="File name is required")
    ext = os.path.splitext(name)[1].lower()
    if ext not in RAG_ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext or '(none)'}",
        )
    return name


@router.post("/upload")
async def upload_to_rag(request: Request, file: UploadFile = File(...)):
    require_user_id(request)

    file_name = _validate_filename(file.filename or "")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        return ingest_rag_upload(file_bytes, file_name)
    except RagServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
