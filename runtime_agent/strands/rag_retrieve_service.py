"""Bedrock Knowledge Base retrieve: pagination, KB id refresh, doc formatting."""

from __future__ import annotations

import json
import logging
from urllib import parse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger("chat")

# Bound Bedrock retrieve pagination to avoid unbounded latency/cost.
MAX_RETRIEVE_PAGES = 5
BOTO_RETRY_CONFIG = Config(retries={"max_attempts": 30, "mode": "standard"})


def _bedrock_retrieve_pages(bedrock_agent_runtime_client, query, kb_id):
    """Call Bedrock retrieve, following nextToken until exhausted or page cap."""
    import chat

    retrieval_results = []
    next_token = None
    for page in range(MAX_RETRIEVE_PAGES):
        params = {
            "retrievalQuery": {"text": query},
            "knowledgeBaseId": kb_id,
            "retrievalConfiguration": {
                "vectorSearchConfiguration": {"numberOfResults": chat.number_of_results},
            },
        }
        if next_token:
            params["nextToken"] = next_token

        response = bedrock_agent_runtime_client.retrieve(**params)
        retrieval_results.extend(response.get("retrievalResults") or [])
        next_token = response.get("nextToken")
        if not next_token:
            break
        logger.info(
            f"retrieve page {page + 1} complete; continuing with nextToken "
            f"(accumulated={len(retrieval_results)})"
        )
    else:
        if next_token:
            logger.warning(
                f"retrieve stopped after {MAX_RETRIEVE_PAGES} pages; "
                "additional results may exist"
            )
    return retrieval_results


def _refresh_knowledge_base_id():
    """List KBs by project name and persist a matching id into chat config."""
    import chat

    bedrock_agent_client = boto3.client(
        "bedrock-agent",
        region_name=chat.bedrock_region,
        config=BOTO_RETRY_CONFIG,
    )
    knowledge_base_summaries = []
    next_token = None
    while True:
        list_kwargs = {}
        if next_token:
            list_kwargs["nextToken"] = next_token
        knowledge_base_page = bedrock_agent_client.list_knowledge_bases(**list_kwargs)
        knowledge_base_summaries.extend(
            knowledge_base_page.get("knowledgeBaseSummaries") or []
        )
        next_token = knowledge_base_page.get("nextToken")
        if not next_token:
            break

    for knowledge_base in knowledge_base_summaries:
        if knowledge_base["name"] == chat.projectName:
            new_knowledge_base_id = knowledge_base["knowledgeBaseId"]
            chat.knowledge_base_id = new_knowledge_base_id
            chat.config["knowledge_base_id"] = new_knowledge_base_id
            with open(chat.config_path, "w", encoding="utf-8") as f:
                json.dump(chat.config, f, ensure_ascii=False, indent=4)
            logger.info(f"Updated knowledge_base_id to: {new_knowledge_base_id}")
            return new_knowledge_base_id
    return None


def _format_retrieval_docs(retrieval_results):
    """Convert Bedrock retrievalResults into RAG json_docs payload."""
    import chat

    json_docs = []
    for result in retrieval_results:
        text = url = name = None
        if "content" in result:
            content = result["content"]
            if "text" in content:
                text = content["text"]

        if "location" in result:
            location = result["location"]
            if "s3Location" in location:
                uri = (
                    location["s3Location"]["uri"]
                    if location["s3Location"]["uri"] is not None
                    else ""
                )
                name = uri.split("/")[-1]
                encoded_name = parse.quote(name)
                url = f"{chat.path}/{chat.doc_prefix}{encoded_name}"
            elif "webLocation" in location:
                url = (
                    location["webLocation"]["url"]
                    if location["webLocation"]["url"] is not None
                    else ""
                )
                name = "WEB"

        page = None
        raw_page = (result.get("metadata") or {}).get(
            "x-amz-bedrock-kb-document-page-number"
        )
        if raw_page is not None:
            try:
                # Bedrock KB uses 0-based page numbers; convert to 1-based for display
                page = int(raw_page) + 1
            except (TypeError, ValueError):
                page = raw_page

        reference = {
            "url": url,
            "title": name,
            "from": "RAG",
        }
        if page is not None:
            reference["page"] = page

        json_docs.append(
            {
                "contents": text,
                "reference": reference,
            }
        )
    logger.info(f"json_docs: {json_docs}")
    return json.dumps(json_docs, ensure_ascii=False)


def retrieve(query):
    """Retrieve KB passages for ``query`` and return a JSON docs string."""
    import chat

    bedrock_agent_runtime_client = boto3.client(
        "bedrock-agent-runtime",
        region_name=chat.bedrock_region,
        config=BOTO_RETRY_CONFIG,
    )

    try:
        retrieval_results = _bedrock_retrieve_pages(
            bedrock_agent_runtime_client, query, chat.knowledge_base_id
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")

        # Update knowledge_base_id only when ResourceNotFoundException occurs
        if error_code == "ResourceNotFoundException":
            logger.warning(f"ResourceNotFoundException occurred: {e}")
            logger.info("Attempting to update knowledge_base_id...")

            try:
                updated_id = _refresh_knowledge_base_id()
            except Exception as list_err:
                logger.error(
                    "Failed to list knowledge bases: %s", type(list_err).__name__
                )
                raise

            if updated_id:
                try:
                    retrieval_results = _bedrock_retrieve_pages(
                        bedrock_agent_runtime_client, query, chat.knowledge_base_id
                    )
                    logger.info("Retry successful after updating knowledge_base_id")
                except Exception as retry_error:
                    logger.error(
                        f"Retry failed after updating knowledge_base_id: {retry_error}"
                    )
                    raise
            else:
                logger.error(
                    f"Could not find knowledge base with name: {chat.projectName}"
                )
                raise
        else:
            logger.error(f"Error retrieving: {e}")
            raise
    except Exception as e:
        logger.error(f"Unexpected error retrieving: {e}")
        raise

    return _format_retrieval_docs(retrieval_results)
