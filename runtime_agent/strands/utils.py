import logging
import sys
import json
import traceback
import boto3
import os

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("utils")

workingDir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(workingDir, "config.json")
PROJECT_NAME_FALLBACK = "strands-runtime"

def _materialize_config(file_path: str, config: dict) -> None:
    """Write config.json so modules that open the file directly still work."""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as write_err:
        logger.warning(f"Could not write fallback config.json: {write_err}")


def _derive_s3_bucket(config: dict) -> str:
    project = (config.get("projectName") or "").strip()
    account = (config.get("accountId") or "").strip()
    region = (config.get("region") or "").strip()
    if project and account and region:
        return f"storage-for-{project}-{account}-{region}"
    return ""


def load_config(path: str | None = None):
    """Load merged config from APP_CONFIG_JSON and a JSON file.

    Runtime images exclude config.json (.dockerignore). AgentCore / ECS inject
    APP_CONFIG_JSON instead; when the file is missing this also materializes it
    so modules that read config.json directly keep working.

    Args:
        path: Optional config file path. Defaults to runtime_agent/strands/config.json.
              Callers outside strands (e.g. add_content.py) can pass
              application/config.json.
    """
    config = {}
    file_path = path or config_path
    had_file = False

    raw = os.environ.get("APP_CONFIG_JSON")
    if raw:
        try:
            config.update(json.loads(raw))
        except Exception as e:
            logger.error(f"Error parsing APP_CONFIG_JSON: {e}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            file_cfg = json.load(f)
        had_file = True
        merged = dict(file_cfg)
        merged.update({k: v for k, v in config.items() if v not in (None, "")})
        config = merged
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        if not config:
            session = boto3.Session()
            region = (
                os.environ.get("AWS_REGION")
                or os.environ.get("AWS_DEFAULT_REGION")
                or session.region_name
            )
            config["region"] = region
            config["projectName"] = os.environ.get("PROJECT_NAME") or PROJECT_NAME_FALLBACK
            if os.environ.get("KNOWLEDGE_BASE_ID"):
                config["knowledge_base_id"] = os.environ["KNOWLEDGE_BASE_ID"]

            try:
                sts = boto3.client("sts")
                response = sts.get_caller_identity()
                config["accountId"] = response["Account"]
            except Exception as sts_err:
                logger.error("STS get_caller_identity failed: %s", sts_err)

    # Env vars always win for critical RAG settings.
    if os.environ.get("KNOWLEDGE_BASE_ID"):
        config["knowledge_base_id"] = os.environ["KNOWLEDGE_BASE_ID"]
    if os.environ.get("PROJECT_NAME"):
        config["projectName"] = os.environ["PROJECT_NAME"]
    if os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"):
        config["region"] = os.environ.get("AWS_REGION") or os.environ.get(
            "AWS_DEFAULT_REGION"
        )
    if os.environ.get("MEMORY_ID"):
        config["memory_id"] = os.environ["MEMORY_ID"]
    if os.environ.get("AGENTCORE_MEMORY_ROLE"):
        config["agentcore_memory_role"] = os.environ["AGENTCORE_MEMORY_ROLE"]

    if not (config.get("s3_bucket") or "").strip():
        derived = _derive_s3_bucket(config)
        if derived:
            config["s3_bucket"] = derived
            if not (config.get("s3_arn") or "").strip():
                config["s3_arn"] = f"arn:aws:s3:::{derived}"

    if config and not had_file:
        _materialize_config(file_path, config)

    return config


def get_config(path: str | None = None) -> dict:
    """Return fresh config (prefer APP_CONFIG_JSON over stale import-time snapshots)."""
    return load_config(path)


def get_sharing_url() -> str:
    """CloudFront sharing base URL, or empty string when unset."""
    return (get_config().get("sharing_url") or "").strip().rstrip("/")


def get_s3_bucket() -> str:
    """Storage bucket name from config / APP_CONFIG_JSON / derived default."""
    return (get_config().get("s3_bucket") or "").strip()


def get_aws_region() -> str:
    cfg = get_config()
    return (
        (cfg.get("region") or "").strip()
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-west-2"
    )


config = load_config()

accountId = config.get('accountId')
if not accountId:
    try:
        sts = boto3.client("sts")
        response = sts.get_caller_identity()
        accountId = response["Account"]
        config['accountId'] = accountId
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as sts_err:
        logger.error("STS get_caller_identity failed at import: %s", sts_err)
        accountId = None

bedrock_region = config.get('region', 'us-west-2')
logger.info(f"bedrock_region: {bedrock_region}")
projectName = config.get('projectName', 'power-trade')
logger.info(f"projectName: {projectName}")

def get_contents_type(file_name):
    if file_name.lower().endswith((".jpg", ".jpeg")):
        content_type = "image/jpeg"
    elif file_name.lower().endswith((".pdf")):
        content_type = "application/pdf"
    elif file_name.lower().endswith((".txt")):
        content_type = "text/plain"
    elif file_name.lower().endswith((".csv")):
        content_type = "text/csv"
    elif file_name.lower().endswith((".ppt", ".pptx")):
        content_type = "application/vnd.ms-powerpoint"
    elif file_name.lower().endswith((".doc", ".docx")):
        content_type = "application/msword"
    elif file_name.lower().endswith((".xls")):
        content_type = "application/vnd.ms-excel"
    elif file_name.lower().endswith((".py")):
        content_type = "text/x-python"
    elif file_name.lower().endswith((".js")):
        content_type = "application/javascript"
    elif file_name.lower().endswith((".md")):
        content_type = "text/markdown"
    elif file_name.lower().endswith((".png")):
        content_type = "image/png"
    elif file_name.lower().endswith((".html", ".htm")):
        content_type = "text/html; charset=utf-8"
    else:
        content_type = "no info"    
    return content_type

# api key to use Tavily Search
def _load_tavily_api_key(app_config: dict) -> str:
    """Load Tavily API key from config.json or Secrets Manager."""
    key = app_config.get("tavily_api_key", "")
    if key:
        return key

    region = app_config.get("region", "us-west-2")
    secret_names = []
    if app_config.get("knowledge_base_name"):
        secret_names.append(f"tavilyapikey-{app_config['knowledge_base_name']}")
    if app_config.get("projectName"):
        secret_names.append(f"tavilyapikey-{app_config['projectName']}")

    secrets_client = boto3.client("secretsmanager", region_name=region)
    for secret_name in dict.fromkeys(secret_names):
        try:
            response = secrets_client.get_secret_value(SecretId=secret_name)
            secret_data = json.loads(response["SecretString"])
            key = secret_data.get("tavily_api_key", "")
            if key:
                logger.info(f"tavily_key loaded from Secrets Manager: {secret_name}")
                return key
        except Exception as e:
            logger.debug(f"Could not load Tavily secret {secret_name}: {e}")
    return ""


tavily_key = _load_tavily_api_key(config)
if tavily_key:
    os.environ["TAVILY_API_KEY"] = tavily_key
    logger.info("tavily_key is configured")
else:
    logger.info("tavily_key is not set.")
