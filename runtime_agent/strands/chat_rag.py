"""RAG retrieve / generate helpers for the chat module.

Public API is re-exported from focused service modules so existing
``from chat_rag import …`` call sites keep working.
"""

from __future__ import annotations

import logging
from urllib import parse

import boto3
from botocore.exceptions import ClientError

from chat_tools import _format_references_markdown
from rag_generate_service import (  # noqa: F401
    DEFAULT_TOP_K,
    MAX_OUTPUT_TOKENS_NON_CLAUDE,
    MAX_REASONING_OUTPUT_TOKENS,
    REASONING_BUDGET_RESERVE_TOKENS,
    run_rag_with_knowledge_base,
)
from rag_retrieve_service import (  # noqa: F401
    BOTO_RETRY_CONFIG,
    MAX_RETRIEVE_PAGES,
    _bedrock_retrieve_pages,
    retrieve,
)

logger = logging.getLogger("chat")


def run_rag_using_retrieve_and_generate(query, notification_queue):
    import chat

    msg = None

    chat.reference_docs = []
    chat.contentList = []

    # retrieve
    if chat.debug_mode == "Enable":
        chat.add_notification(notification_queue, f"RAG 검색을 수행합니다. 검색어: {query}")

    bedrock_agent_runtime_client = boto3.client(
        "bedrock-agent-runtime",
        region_name=chat.bedrock_region
    )

    model_arn = f"arn:aws:bedrock:{chat.region}:{chat.account_id}:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    try:
        retrieve_response = bedrock_agent_runtime_client.retrieve_and_generate_stream(
            input={"text": query},
            retrieveAndGenerateConfiguration={
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": chat.knowledge_base_id,
                    "modelArn": model_arn,
                    "retrievalConfiguration": {
                        "vectorSearchConfiguration": {
                            "numberOfResults": chat.number_of_results
                        }
                    }
                },
                "type": "KNOWLEDGE_BASE"
            }
        )
        logger.info(f"retrieve_response type: {type(retrieve_response)}")

        msg = ""
        for event in retrieve_response['stream']:
            if "output" in event:
                text = event['output']['text']
                logger.info(f"text: {text}")
                msg += text

                chat.update_rag_result(notification_queue, msg)

            if "citation" in event:
                citation = event['citation']
                logger.info(f"citation: {citation}")

                retrieved_references = citation.get('citation', {}).get('retrievedReferences', []) or citation.get('retrievedReferences', [])

                for ref in retrieved_references:
                    content_text = url = name = ""

                    if "content" in ref:
                        content_text = ref["content"]["text"]

                    if "location" in ref:
                        location = ref["location"]
                        if "s3Location" in location:
                            uri = location["s3Location"]["uri"] if location["s3Location"]["uri"] is not None else ""

                            name = uri.split("/")[-1]
                            encoded_name = parse.quote(name)
                            url = f"{chat.path}/{chat.doc_prefix}{encoded_name}"

                        if "webLocation" in location:
                            url = location["webLocation"]["url"] if location["webLocation"]["url"] is not None else ""
                            name = "WEB"

                    reference_doc = {
                        "contents": content_text,
                        "reference": {
                            "url": url,
                            "title": name,
                            "from": "RAG"
                        }
                    }

                    # duplicate check and add to reference_docs
                    if reference_doc not in chat.reference_docs:
                        chat.reference_docs.append(reference_doc)

        if chat.reference_docs:
            refs = [
                {
                    "title": doc["reference"]["title"],
                    "url": doc["reference"]["url"],
                    "content": doc["contents"],
                }
                for doc in chat.reference_docs
            ]
            msg += _format_references_markdown(refs)

        chat.update_rag_result(notification_queue, msg)

        return msg
    except ClientError as e:
        logger.error(
            f"retrieve_and_generate_stream failed: {e}",
            exc_info=True,
        )
        err_msg = "RAG 검색에 실패했습니다."
        chat.add_notification(notification_queue, err_msg)
        return err_msg
    except Exception as e:
        logger.error(
            f"retrieve_and_generate_stream failed: {e}",
            exc_info=True,
        )
        err_msg = "RAG 검색에 실패했습니다."
        chat.add_notification(notification_queue, err_msg)
        return err_msg
