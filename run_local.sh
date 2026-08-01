#!/usr/bin/env bash
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

# Local build + run: frontend (npm) then FastAPI (uvicorn) on :8501
# If port 8501 is already in use, the existing process is stopped first.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8501

cd "$ROOT"

echo "==> Frontend build (application/web)"
cd application/web
npm install
npm run build
cd "$ROOT"

echo "==> Freeing port ${PORT} if occupied"
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${PIDS}" ]]; then
    echo "    Port ${PORT} in use by PID(s): ${PIDS} — killing"
    # shellcheck disable=SC2086
    kill ${PIDS} 2>/dev/null || true
    sleep 1
    # Force-kill if still listening
    PIDS="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "${PIDS}" ]]; then
      # shellcheck disable=SC2086
      kill -9 ${PIDS} 2>/dev/null || true
      sleep 0.5
    fi
  else
    echo "    Port ${PORT} is free"
  fi
else
  echo "    lsof not found; skipping port check"
fi

echo "==> Starting uvicorn on 0.0.0.0:${PORT}"
echo "    Open http://localhost:${PORT}"
exec uvicorn application.server:app --host 0.0.0.0 --port "${PORT}"
