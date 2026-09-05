import logging
import sys
import json
import traceback
import boto3
import os
from pathlib import Path
from typing import Any

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

# Huge MCP/tool payloads (e.g. raw HTML) overwhelm non-blocking stdout and SSE.
LOG_TRUNCATE_CHARS = 2_000
STREAM_TRUNCATE_CHARS = 8_000
_TRUNCATE_SUFFIX = "\n...[truncated {omitted} chars]"


def truncate_text(text: object, max_chars: int, *, suffix_template: str = _TRUNCATE_SUFFIX) -> str:
    """Return a string capped at max_chars for safe logging / SSE display."""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False, default=str)
        except TypeError:
            text = str(text)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    suffix = suffix_template.format(omitted=omitted)
    keep = max(0, max_chars - len(suffix))
    return text[:keep] + suffix


def truncate_for_log(text: object, max_chars: int = LOG_TRUNCATE_CHARS) -> str:
    return truncate_text(text, max_chars)


def truncate_for_stream(text: object, max_chars: int = STREAM_TRUNCATE_CHARS) -> str:
    return truncate_text(text, max_chars)

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


def _default_session_storage_dir() -> str:
    for candidate in ("/mnt/workspace", "/mnt/app-data"):
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(workingDir, ".session_storage")


SESSION_STORAGE_DIR = os.environ.get("SESSION_STORAGE_DIR") or _default_session_storage_dir()


def sanitize_user_path_segment(user_id: str | None) -> str | None:
    """Return a safe single path segment for per-user workspace folders, or None."""
    if not user_id:
        return None
    segment = (
        str(user_id)
        .strip()
        .replace("/", "_")
        .replace("\\", "_")
        .replace("..", "_")
    )
    return segment or None


def get_user_graph_dir(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/graph (does not create)."""
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        segment = "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "graph")

def get_user_wiki_dir(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/wiki (does not create)."""
    segment = sanitize_user_path_segment(user_id)
    if not segment:
        segment = "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "wiki")


def wiki_graphify_out_dir(user_id: str | None = None) -> str:
    return os.path.join(get_user_wiki_dir(user_id), "graphify-out")


def wiki_graph_json_path(user_id: str | None = None) -> str:
    return os.path.join(wiki_graphify_out_dir(user_id), "graph.json")


WIKI_SYNC_STATUS_FILENAME = ".wiki_sync_status.json"


def wiki_sync_status_path(user_id: str | None = None) -> str:
    return os.path.join(wiki_graphify_out_dir(user_id), WIKI_SYNC_STATUS_FILENAME)


def load_wiki_sync_status(user_id: str | None = None) -> dict[str, Any] | None:
    """Load mirrored ``.wiki_sync_status.json`` written by Application wiki_jobs."""
    path = wiki_sync_status_path(user_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def wiki_recall_blocked_message(
    user_id: str | None, graph_json_path: str | os.PathLike[str]
) -> str | None:
    """Return a user-facing error when ``recall_wiki`` should not run yet."""
    graph_json = Path(graph_json_path)
    status_doc = load_wiki_sync_status(user_id)
    if status_doc:
        st = str(status_doc.get("status") or "").strip().lower()
        if st in ("queued", "running"):
            msg = str(status_doc.get("message") or "").strip()
            base = msg or "Wiki 동기화가 진행 중입니다."
            return (
                f"{base} 완료 후(보통 1–3분) 다시 검색해 주세요. "
                "Settings → Wiki → Graph에서 진행 상태를 확인할 수 있습니다."
            )
        if st == "error" and not graph_json.is_file():
            err = str(
                status_doc.get("error") or status_doc.get("message") or "알 수 없는 오류"
            ).strip()
            return (
                f"Wiki 동기화에 실패했습니다: {err}. "
                "Settings → Wiki → Sync를 다시 실행하세요."
            )

    if not graph_json.is_file():
        return (
            "Wiki 그래프가 아직 없습니다. Settings → Wiki → Sync를 실행한 뒤 "
            "동기화가 완료되면 다시 검색하세요."
        )
    return None


def wiki_sources_path(user_id: str | None = None) -> str:
    return os.path.join(get_user_wiki_dir(user_id), "wiki_sources.json")


def get_wiki_source_folders(user_id: str | None = None) -> list[str]:
    """Configured Wiki Sync source folders (max 3). Empty → Sync uses raw/wiki root."""
    path = wiki_sources_path(user_id)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    folders = raw.get("AGENT_WIKI_SOURCES") if isinstance(raw, dict) else None
    if not isinstance(folders, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in folders:
        pth = str(item or "").strip()
        if not pth:
            continue
        abs_path = os.path.abspath(os.path.expanduser(pth))
        if abs_path in seen:
            continue
        seen.add(abs_path)
        out.append(abs_path)
        if len(out) >= 3:
            break
    return out



_DEFAULT_USER_SETTINGS: dict[str, object] = {
    "knowledge_graph_enabled": True,
}


def get_user_settings_path(user_id: str | None) -> str:
    """Absolute path to {SESSION_STORAGE_DIR}/{user_id}/settings.json (does not create)."""
    segment = sanitize_user_path_segment(user_id) or "default"
    return os.path.join(SESSION_STORAGE_DIR, segment, "settings.json")


def load_user_settings(user_id: str | None) -> dict[str, object]:
    """Load per-user UI/feature settings. Missing file → defaults (KG on)."""
    settings = dict(_DEFAULT_USER_SETTINGS)
    path = get_user_settings_path(user_id)
    if not os.path.isfile(path):
        return settings
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and "knowledge_graph_enabled" in raw:
            settings["knowledge_graph_enabled"] = bool(raw["knowledge_graph_enabled"])
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load user settings %s: %s", path, e)
    return settings


def is_knowledge_graph_enabled(user_id: str | None) -> bool:
    """True when Knowledge Graph feature is on (default)."""
    return bool(load_user_settings(user_id).get("knowledge_graph_enabled", True))


def is_hybrid_graph_search_enabled() -> bool:
    """True when config.json hybrid_graph_search is enable (embedding vector search)."""
    cfg = load_config() or {}
    raw = str(cfg.get("hybrid_graph_search") or "").strip().lower()
    return raw in {"enable", "enabled", "on", "true", "1", "yes"}


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
