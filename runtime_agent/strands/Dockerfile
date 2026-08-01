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

FROM --platform=linux/arm64 python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    build-essential \
    python3-dev \
    nodejs \
    npm \
    pandoc \
    poppler-utils \
    libreoffice-writer \
    libreoffice-calc \
    libreoffice-impress \
    fonts-liberation \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --upgrade "setuptools>=83.0.0" \
    && pip install "urllib3>=2.7.0" boto3 botocore --upgrade
# Keep FastMCP (mcp.server.fastmcp). mcp 2.x removed it; pin 1.x for stdio MCP servers.
RUN pip install "mcp>=1.9.0,<2"
RUN pip install "strands-agents[openai,otel]>=1.44.0" "strands-agents-tools>=0.8.1"
# CVE-2026-16796 fixed in bedrock-agentcore>=1.18.1
RUN pip install "bedrock-agentcore>=1.18.1" uv
RUN pip install "mcp>=1.9.0,<2"
RUN pip install Pillow pytz tavily-python requests beautifulsoup4
RUN pip install langchain-aws langchain-core langchain-text-splitters PyPDF2
RUN pip install python-docx openpyxl reportlab matplotlib pandas finance-datareader pypdf pdfplumber pyyaml
RUN pip install colorama rich defusedxml lxml "markitdown[pptx]" python-pptx pdf2image pytesseract

# Skills that use docx-js / pptxgenjs via bash/node (local install for require())
# pptx skill also uses react-icons + sharp for icon rasterization
RUN npm install docx pptxgenjs react react-dom react-icons sharp
ENV NODE_PATH=/app/node_modules

# OpenTelemetry (ADOT + Strands telemetry for AgentCore Evaluations)
RUN pip install aws-opentelemetry-distro>=0.10.0

COPY . .

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

ENV PYTHONPATH=/app
ENV ALLOW_MISSING_RAG_CONFIG=1
ENV BYPASS_TOOL_CONSENT=true
ENV OTEL_PYTHON_DISTRO=aws_distro
ENV OTEL_PYTHON_CONFIGURATOR=aws_configurator
ENV OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true
ARG OTEL_SERVICE_NAME=strands_runtime.DEFAULT
ENV OTEL_RESOURCE_ATTRIBUTES=service.name=${OTEL_SERVICE_NAME}

EXPOSE 8080

# AgentCore runtimes expose /ping for health probes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl --fail http://localhost:8080/ping || exit 1

CMD ["uv", "run", "opentelemetry-instrument", "uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]
