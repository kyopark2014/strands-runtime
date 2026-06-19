FROM --platform=linux/arm64 python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    build-essential \
    python3-dev \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install boto3 botocore --upgrade
RUN pip install mcp
RUN pip install "strands-agents[openai]>=1.44.0" "strands-agents-tools>=0.8.1"
RUN pip install bedrock-agentcore uv
RUN pip install Pillow pytz tavily-python requests beautifulsoup4
RUN pip install langchain-aws langchain-core langchain-text-splitters PyPDF2
RUN pip install python-docx openpyxl reportlab matplotlib pandas finance-datareader pypdf pdfplumber pyyaml

# Skills that use docx-js / pptxgenjs via bash/node (local install for require())
RUN npm install docx pptxgenjs
ENV NODE_PATH=/app/node_modules

# OpenTelemetry
RUN pip install aws-opentelemetry-distro>=0.10.0

COPY . .

ENV PYTHONPATH=/app
ENV ALLOW_MISSING_RAG_CONFIG=1
ENV BYPASS_TOOL_CONSENT=true

EXPOSE 8080

CMD ["uv", "run", "opentelemetry-instrument", "uvicorn", "agent:app", "--host", "0.0.0.0", "--port", "8080"]
