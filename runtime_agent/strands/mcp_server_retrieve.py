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
import sys
import os
import importlib.util
import time

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

_retrieve_module_path = os.path.join(_script_dir, "mcp_retrieve.py")
_spec = importlib.util.spec_from_file_location("mcp_retrieve", _retrieve_module_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load mcp_retrieve from {_retrieve_module_path}")
_mcp_retrieve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mcp_retrieve)

from mcp.server.fastmcp import FastMCP 

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("retrieve-server")

_RETRIEVE_MAX_ATTEMPTS = 3
_RETRIEVE_RETRY_BASE_DELAY_SECONDS = 0.5

try:
    mcp = FastMCP(
        name = "mcp-retrieve",
        instructions=(
            "You are a helpful assistant. "
            "You retrieve documents in RAG."
        ),
    )
    logger.info("MCP server initialized successfully")
except Exception as e:
        err_msg = f"Error: {str(e)}"
        logger.info(f"{err_msg}")

######################################
# RAG
######################################
@mcp.tool()
def retrieve(keyword: str) -> str:
    """
    Query the keyword using RAG based on the knowledge base.
    keyword: the keyword to query
    return: the result of query
    """
    logger.info(f"search --> keyword: {keyword}")

    if not _mcp_retrieve.knowledge_base_id:
        return (
            "Knowledge Base ID가 설정되지 않아 조회가 불가능합니다. "
            "AgentCore Runtime 환경변수 KNOWLEDGE_BASE_ID 또는 config.json의 "
            "knowledge_base_id를 설정하세요."
        )

    last_error: BaseException | None = None
    for attempt in range(1, _RETRIEVE_MAX_ATTEMPTS + 1):
        try:
            return _mcp_retrieve.retrieve(keyword)
        except Exception as error:
            last_error = error
            if attempt >= _RETRIEVE_MAX_ATTEMPTS:
                break
            logger.warning(
                "Knowledge base retrieval failed (attempt %s/%s): %s",
                attempt,
                _RETRIEVE_MAX_ATTEMPTS,
                type(error).__name__,
            )
            time.sleep(_RETRIEVE_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
    logger.error(
        "Knowledge base retrieval failed for keyword=%r after %s attempts",
        keyword,
        _RETRIEVE_MAX_ATTEMPTS,
        exc_info=last_error,
    )
    return "Knowledge base retrieval failed"

if __name__ =="__main__":
    mcp.run(transport="stdio")


