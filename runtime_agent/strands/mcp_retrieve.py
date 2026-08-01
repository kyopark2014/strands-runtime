import logging
import sys
import os
import json
from urllib import parse
from botocore.config import Config
from botocore.exceptions import ClientError

from aws_client_factory import create_boto3_client

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("retrieve")

script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, "config.json")

def load_config():
    from config_loader import load_json_config

    return load_json_config(config_path, env_json_key="APP_CONFIG_JSON")

config = load_config()

bedrock_region = (
    os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or config.get("region", "us-west-2")
)
projectName = os.environ.get("PROJECT_NAME") or config.get("projectName")
knowledge_base_id = os.environ.get("KNOWLEDGE_BASE_ID") or config.get("knowledge_base_id")
DEFAULT_RETRIEVAL_RESULTS = 5
number_of_results = DEFAULT_RETRIEVAL_RESULTS

doc_prefix = "docs/"
path = config.get("sharing_url", "") or os.environ.get("SHARING_URL", "")

# Bound Bedrock retrieve pagination; boto3 retries for transient failures.
MAX_RETRIEVE_PAGES = 5
BOTO_RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})

logger.info(
    f"retrieve config: projectName={projectName} knowledge_base_id={knowledge_base_id} region={bedrock_region}"
)

aws_access_key = config.get('aws', {}).get('access_key_id')
aws_secret_key = config.get('aws', {}).get('secret_access_key')
aws_session_token = config.get('aws', {}).get('session_token')

bedrock_agent_runtime_client = create_boto3_client(
    "bedrock-agent-runtime",
    region_name=bedrock_region,
    config=BOTO_RETRY_CONFIG,
    access_key=aws_access_key,
    secret_key=aws_secret_key,
    session_token=aws_session_token,
)


def _bedrock_retrieve_pages(query, kb_id):
    """Call Bedrock retrieve, following nextToken until exhausted or page cap."""
    retrieval_results = []
    next_token = None
    for page in range(MAX_RETRIEVE_PAGES):
        params = {
            "retrievalQuery": {"text": query},
            "knowledgeBaseId": kb_id,
            "retrievalConfiguration": {
                "vectorSearchConfiguration": {"numberOfResults": number_of_results},
            },
        }
        if next_token:
            params["nextToken"] = next_token
        response = bedrock_agent_runtime_client.retrieve(**params)
        retrieval_results.extend(response.get("retrievalResults") or [])
        next_token = response.get("nextToken")
        if not next_token:
            break
        logger.info(  # nosemgrep: python.lang.security.audit.logging.python-logger-credential-disclosure
            "retrieve page %s complete; continuing with nextToken (accumulated=%s)",
            page + 1,
            len(retrieval_results),
        )
    else:
        if next_token:
            logger.warning(
                "retrieve stopped after %s pages; additional results may exist",
                MAX_RETRIEVE_PAGES,
            )
    return retrieval_results


def _resolve_knowledge_base_id():
    """Look up knowledge base by project name; return id or None on failure."""
    region = config.get("region", "us-west-2")
    name = config.get("projectName")
    try:
        bedrock_agent_client = create_boto3_client(
            "bedrock-agent",
            region_name=region,
            config=BOTO_RETRY_CONFIG,
        )
        knowledge_base_summaries = []
        next_token = None
        while True:
            list_kwargs = {}
            if next_token:
                list_kwargs["nextToken"] = next_token
            knowledge_base_page = bedrock_agent_client.list_knowledge_bases(
                **list_kwargs
            )
            knowledge_base_summaries.extend(
                knowledge_base_page.get("knowledgeBaseSummaries") or []
            )
            next_token = knowledge_base_page.get("nextToken")
            if not next_token:
                break
    except Exception as e:
        logger.error("Failed to list knowledge bases: %s", type(e).__name__)
        return None

    for knowledge_base in knowledge_base_summaries:
        if knowledge_base["name"] == name:
            return knowledge_base["knowledgeBaseId"]
    return None


def retrieve(query):
    global knowledge_base_id
    
    try:
        retrieval_results = _bedrock_retrieve_pages(query, knowledge_base_id)
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        
        if error_code == "ResourceNotFoundException":
            logger.warning(f"ResourceNotFoundException occurred: {e}")
            logger.info("Attempting to update knowledge_base_id...")

            new_knowledge_base_id = _resolve_knowledge_base_id()
            if new_knowledge_base_id:
                knowledge_base_id = new_knowledge_base_id
                config["knowledge_base_id"] = new_knowledge_base_id
                try:
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(config, f, ensure_ascii=False, indent=4)
                except OSError as write_err:
                    logger.warning("Failed to persist knowledge_base_id: %s", write_err)
                logger.info(f"Updated knowledge_base_id to: {new_knowledge_base_id}")
                try:
                    retrieval_results = _bedrock_retrieve_pages(query, knowledge_base_id)
                    logger.info("Retry successful after updating knowledge_base_id")
                except Exception as retry_error:
                    logger.error(f"Retry failed after updating knowledge_base_id: {retry_error}")
                    raise
            else:
                logger.error(
                    "Could not find knowledge base with name: %s",
                    config.get("projectName"),
                )
                raise
        else:
            # Re-raise other errors that are not ResourceNotFoundException
            logger.error(f"Error retrieving: {e}")
            raise
    except Exception as e:
        # Re-raise other exceptions that are not ClientError
        logger.error(f"Unexpected error retrieving: {e}")
        raise

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
                uri = location["s3Location"]["uri"] if location["s3Location"]["uri"] is not None else ""
                
                name = uri.split("/")[-1]
                encoded_name = parse.quote(name)                
                url = f"{path}/{doc_prefix}{encoded_name}"
                
            elif "webLocation" in location:
                url = location["webLocation"]["url"] if location["webLocation"]["url"] is not None else ""
                name = "WEB"

        page = None
        raw_page = (result.get("metadata") or {}).get("x-amz-bedrock-kb-document-page-number")
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

        json_docs.append({
            "contents": text,
            "reference": reference,
        })
    logger.info(f"json_docs: {json_docs}")

    return json.dumps(json_docs, ensure_ascii=False)