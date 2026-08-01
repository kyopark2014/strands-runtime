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

"""RAG generation: prompt construction, Bedrock invoke, response parsing."""

from __future__ import annotations

import json
import logging

import boto3
from botocore.config import Config

from chat_tools import _format_references_markdown
from rag_retrieve_service import retrieve

logger = logging.getLogger("chat")

MAX_OUTPUT_TOKENS_NON_CLAUDE = 5120
MAX_REASONING_OUTPUT_TOKENS = 64000
REASONING_BUDGET_RESERVE_TOKENS = 1000
DEFAULT_TOP_K = 250


def _build_rag_prompt(query: str, relevant_context: str) -> tuple[str, str]:
    import chat

    if chat.isKorean(query):
        system_prompt = (
            "다음의 컨텍스트를 사용하여 질문에 답변하세요. "
            "컨텍스트에 정보가 없으면 모른다고 답변하세요. "
            "답변은 <result> 태그 안에 작성하세요."
        )
    else:
        system_prompt = (
            "Answer the question using the following context. "
            "If you don't know the answer based on the context, say you don't know. "
            "Put your answer in <result> tags."
        )
    user_message = f"Question: {query}\n\nContext:\n{relevant_context}"
    return system_prompt, user_message


def _build_model_parameters() -> dict:
    import chat

    if chat.model_type == "claude":
        max_output_tokens = chat.get_max_output_tokens(chat.model_id)
        stop_sequence = "\n\nHuman:"
    else:
        max_output_tokens = MAX_OUTPUT_TOKENS_NON_CLAUDE
        stop_sequence = '"\n\n<thinking>", "\n<thinking>", " <thinking>"'

    if chat.reasoning_mode == "Enable" and not chat.uses_adaptive_thinking(chat.model_id):
        max_reasoning_output_tokens = MAX_REASONING_OUTPUT_TOKENS
        thinking_budget = min(
            max_output_tokens,
            max_reasoning_output_tokens - REASONING_BUDGET_RESERVE_TOKENS,
        )
        return {
            "max_tokens": max_reasoning_output_tokens,
            "temperature": 1,
            "thinking": {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            },
            "stop_sequences": [stop_sequence],
        }

    parameters = {
        "max_tokens": max_output_tokens,
        "stop_sequences": [stop_sequence],
    }
    if not chat.is_fable_model(chat.model_id) and not chat.uses_adaptive_thinking(
        chat.model_id
    ):
        parameters["temperature"] = 0.1
        parameters["top_k"] = DEFAULT_TOP_K
        parameters["top_p"] = 0.9
    return parameters


def _build_request_body(system_prompt: str, user_message: str, parameters: dict) -> dict:
    import chat

    if chat.model_type == "claude":
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": parameters["max_tokens"],
            "stop_sequences": parameters.get("stop_sequences", []),
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": user_message,
                }
            ],
        }
        if "temperature" in parameters:
            request_body["temperature"] = parameters["temperature"]
        if "top_k" in parameters:
            request_body["top_k"] = parameters["top_k"]
        if "top_p" in parameters:
            request_body["top_p"] = parameters["top_p"]
        if "thinking" in parameters:
            request_body["thinking"] = parameters["thinking"]
        return request_body

    request_body = {
        "max_tokens": parameters["max_tokens"],
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": user_message,
            }
        ],
    }
    if "temperature" in parameters:
        request_body["temperature"] = parameters["temperature"]
    return request_body


def _extract_response_text(response_body: dict) -> str:
    import chat

    invalid_response_msg = "Invalid response format from model"

    if chat.model_type == "claude":
        if "content" in response_body:
            content = response_body["content"]
            if isinstance(content, list) and len(content) > 0:
                return content[0].get("text", "")
            logger.warning("Unexpected Claude content structure: %s", content)
            return invalid_response_msg
        logger.warning("Claude response missing content key: %s", response_body)
        return invalid_response_msg

    if "outputs" in response_body:
        return response_body.get("outputs", [{}])[0].get("text", "")
    logger.warning("Non-Claude response missing outputs key: %s", response_body)
    return invalid_response_msg


def _append_references(msg: str, relevant_docs: list) -> str:
    if not relevant_docs:
        return msg
    refs = []
    for doc in relevant_docs:
        ref = {
            "title": doc["reference"]["title"],
            "url": doc["reference"]["url"],
            "content": doc["contents"],
        }
        if doc["reference"].get("page") is not None:
            ref["page"] = doc["reference"]["page"]
        refs.append(ref)
    return msg + _format_references_markdown(refs)


def run_rag_with_knowledge_base(query, st):
    """Retrieve context, invoke the configured model, and return the answer text."""
    import chat
    import bedrock_data_retention

    chat.reference_docs = []
    chat.contentList = []

    if chat.debug_mode == "Enable":
        st.info(f"RAG 검색을 수행합니다. 검색어: {query}")

    json_docs = retrieve(query)
    logger.info(f"json_docs: {json_docs}")

    relevant_docs = json.loads(json_docs)

    relevant_context = ""
    for doc in relevant_docs:
        relevant_context += f"{doc['contents']}\n\n"

    st.info(f"{len(relevant_docs)}개의 관련된 문서를 얻었습니다.")

    if "fable" in chat.model_id.lower():
        bedrock_data_retention.ensure_fable_data_retention(
            chat.model_id,
            bedrock_region=chat.bedrock_region,
        )

    bedrock_client = boto3.client(
        service_name="bedrock-runtime",
        region_name=chat.bedrock_region,
        config=Config(
            retries={
                "max_attempts": 30,
            }
        ),
    )

    system_prompt, user_message = _build_rag_prompt(query, relevant_context)
    parameters = _build_model_parameters()

    msg = ""
    try:
        request_body = _build_request_body(system_prompt, user_message, parameters)
        response = bedrock_client.invoke_model(
            modelId=chat.model_id,
            body=json.dumps(request_body),
        )

        response_body = json.loads(response["body"].read())
        logger.info(f"response_body: {response_body}")

        msg = _extract_response_text(response_body)
        logger.info(f"result: {msg}")

        if msg.find("<result>") != -1:
            msg = msg[msg.find("<result>") + 8 : msg.find("</result>")]

    except Exception as e:
        # Log the full traceback server-side only; surface a generic message.
        logger.exception("LLM request failed")
        raise RuntimeError("Not able to request to LLM") from e

    return _append_references(msg, relevant_docs)
