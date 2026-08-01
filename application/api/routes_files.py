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

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from application.api.routes_auth import require_user_id
from application.services.file_upload_service import (
    FileUploadServiceError,
    sanitize_image_filename,
    upload_chat_image,
)

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Upload an image to S3 (images/) for chat attachment. No Knowledge Base sync."""
    require_user_id(request)

    try:
        file_name = sanitize_image_filename(file.filename or "pasted.png")
        file_bytes = await file.read()
        return upload_chat_image(file_bytes, file_name)
    except FileUploadServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
