/**
 * Copyright 2026 Amazon.com, Inc. or its affiliates
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { api } from "../api";

/** Normalize any thrown value into an Error with a safe, user-facing message. */
function toUploadError(context: string, cause: unknown): Error {
  // Never forward raw exception text (stack fragments, SDK internals) to the UI.
  const error = new Error(context);
  if (cause instanceof Error) {
    (error as Error & { cause?: unknown }).cause = cause;
  }
  return error;
}

/** Service layer for file uploads — keeps hooks free of direct API calls. */
export const fileUploadService = {
  async uploadImage(file: File): Promise<{ url: string; file_name: string }> {
    try {
      return await api.uploadFile(file);
    } catch (cause) {
      throw toUploadError("Image upload failed", cause);
    }
  },

  async uploadToRag(file: File): Promise<{ message: string }> {
    try {
      return await api.uploadToRag(file);
    } catch (cause) {
      throw toUploadError("RAG upload failed", cause);
    }
  },
};
