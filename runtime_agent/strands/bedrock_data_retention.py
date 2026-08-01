import json
import logging
import os
import time
import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError

logger = logging.getLogger(__name__)

OPTED_IN_REGIONS: set[str] = set()
FABLE_RETENTION_ENSURED = False
BEDROCK_DATA_RETENTION_URL = "https://bedrock.{region}.amazonaws.com/data-retention"
# bedrock-mantle is the Amazon Bedrock OpenAI-compatible endpoint (not a separate AWS
# service): https://docs.aws.amazon.com/bedrock/latest/userguide/bedrock-mantle.html
MANTLE_DATA_RETENTION_URL = "https://bedrock-mantle.{region}.api.aws/v1/data_retention"
OPT_IN_MODE = "provider_data_share"
DEFAULT_REGION = "us-east-1"
FABLE_BEDROCK_REGIONS = ("us-west-2", "us-east-1", "us-east-2")
CONFIG_KEY = "fable_data_retention_opt_in"

# Idempotent GET/PUT against Bedrock control plane / mantle — retry transient failures.
HTTP_MAX_ATTEMPTS = 3
HTTP_RETRY_BASE_DELAY_SECONDS = 1.0
HTTP_TIMEOUT_SECONDS = 30
_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _get_account_id() -> str:
    import utils

    account_id = utils.config.get("accountId")
    if account_id:
        return str(account_id)

    sts = boto3.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    utils.config["accountId"] = account_id
    return str(account_id)


def _is_fable_opt_in_recorded(account_id: str) -> bool:
    import utils

    recorded = utils.config.get(CONFIG_KEY)
    if isinstance(recorded, dict):
        return (
            recorded.get("completed") is True
            and str(recorded.get("account_id", "")) == account_id
        )
    return recorded is True


def _record_fable_opt_in(account_id: str) -> None:
    import utils

    utils.config[CONFIG_KEY] = {
        "completed": True,
        "account_id": account_id,
    }
    try:
        with open(utils.config_path, "w", encoding="utf-8") as config_file:
            json.dump(utils.config, config_file, indent=2, ensure_ascii=False)
        logger.info(
            "Recorded Fable data retention opt-in in config.json for account %s",
            account_id,
        )
    except Exception as error:
        logger.warning("Failed to record Fable opt-in in config.json: %s", error)


def _get_bearer_token(region: str) -> str:
    token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if token:
        return token

    from aws_bedrock_token_generator import provide_token

    return provide_token(region=region)


def get_bedrock_bearer_token(region: str) -> str:
    return _get_bearer_token(region)


def _urlopen_with_retry(
    http_request: urllib.request.Request,
    *,
    operation: str,
) -> tuple[int, str]:
    """Perform an idempotent HTTP call with exponential backoff on transient errors."""
    # Reject non-HTTPS schemes (e.g. file:/ftp:) before opening the URL.
    if http_request.type != "https":
        raise ValueError(f"Refusing to open non-HTTPS URL scheme: {http_request.type}")
    last_error: BaseException | None = None
    for attempt in range(1, HTTP_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(  # nosec B310 — scheme validated above
                http_request, timeout=HTTP_TIMEOUT_SECONDS
            ) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in _RETRYABLE_HTTP_STATUS or attempt >= HTTP_MAX_ATTEMPTS:
                raise
            logger.warning(
                "%s HTTP %s (attempt %s/%s)",
                operation,
                error.code,
                attempt,
                HTTP_MAX_ATTEMPTS,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt >= HTTP_MAX_ATTEMPTS:
                raise
            logger.warning(
                "%s failed (attempt %s/%s): %s",
                operation,
                attempt,
                HTTP_MAX_ATTEMPTS,
                type(error).__name__,
            )
        time.sleep(HTTP_RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def _request_bedrock_control_plane(
    method: str, region: str, body: dict | None = None
) -> tuple[int, str]:
    try:
        credentials = boto3.Session().get_credentials()
        if credentials is None:
            raise NoCredentialsError()
        frozen = credentials.get_frozen_credentials()
    except (NoCredentialsError, PartialCredentialsError, ClientError, BotoCoreError, AttributeError):
        logger.exception("Failed to retrieve AWS credentials for Bedrock control plane")
        raise RuntimeError("Failed to retrieve AWS credentials") from None
    url = BEDROCK_DATA_RETENTION_URL.format(region=region)
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if payload else {}
    request = AWSRequest(method=method, url=url, data=payload, headers=headers)
    SigV4Auth(frozen, "bedrock", region).add_auth(request)
    prepared = request.prepare()
    http_request = urllib.request.Request(
        prepared.url,
        data=payload,
        method=method,
        headers=dict(prepared.headers),
    )
    return _urlopen_with_retry(
        http_request,
        operation=f"bedrock control plane {method} {region}",
    )


def _request_mantle(method: str, region: str, body: dict | None = None) -> tuple[int, str]:
    token = _get_bearer_token(region)
    url = MANTLE_DATA_RETENTION_URL.format(region=region)
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    return _urlopen_with_retry(
        request,
        operation=f"bedrock-mantle {method} {region}",
    )


def get_data_retention_mode(region: str = DEFAULT_REGION) -> tuple[bool, str]:
    try:
        status, body = _request_bedrock_control_plane("GET", region)
        logger.info("Data retention GET %s: HTTP %s", region, status)
        return True, f"Data retention mode retrieved (HTTP {status})"
    except urllib.error.HTTPError as error:
        logger.warning(
            "Data retention GET failed for %s: HTTP %s",
            region,
            error.code,
            exc_info=True,
        )
        return False, "Failed to retrieve data retention mode"
    except Exception as error:
        logger.warning(
            "Data retention GET failed for %s: %s",
            region,
            type(error).__name__,
            exc_info=True,
        )
        return False, "Failed to retrieve data retention mode"


def opt_in_provider_data_share(region: str = DEFAULT_REGION) -> tuple[bool, str]:
    if region in OPTED_IN_REGIONS:
        return True, ""

    try:
        status, _body = _request_bedrock_control_plane(
            "PUT", region, {"mode": OPT_IN_MODE}
        )
        OPTED_IN_REGIONS.add(region)
        logger.info("Data retention opt-in via control plane %s: HTTP %s", region, status)
        return True, f"Data retention opt-in succeeded for {region}"
    except urllib.error.HTTPError as control_plane_error:
        logger.warning(
            "Control plane opt-in failed for %s: HTTP %s",
            region,
            control_plane_error.code,
            exc_info=True,
        )
    except Exception as control_plane_error:
        logger.warning(
            "Control plane opt-in failed for %s: %s",
            region,
            type(control_plane_error).__name__,
            exc_info=True,
        )

    try:
        status, _body = _request_mantle("PUT", region, {"mode": OPT_IN_MODE})
        OPTED_IN_REGIONS.add(region)
        logger.info("Data retention opt-in via mantle %s: HTTP %s", region, status)
        return True, f"Data retention opt-in succeeded for {region}"
    except urllib.error.HTTPError as mantle_error:
        logger.warning(
            "Mantle opt-in failed for %s: HTTP %s",
            region,
            mantle_error.code,
            exc_info=True,
        )
        return False, f"Failed to opt in for data retention in {region}"
    except Exception as mantle_error:
        logger.warning(
            "Mantle opt-in failed for %s: %s",
            region,
            type(mantle_error).__name__,
            exc_info=True,
        )
        return False, f"Failed to opt in for data retention in {region}"


def ensure_fable_data_retention(
    model_id: str,
    bedrock_region: str = DEFAULT_REGION,
) -> bool:
    global FABLE_RETENTION_ENSURED

    if "fable" not in model_id.lower():
        return True

    if FABLE_RETENTION_ENSURED:
        return True

    account_id = _get_account_id()
    if _is_fable_opt_in_recorded(account_id):
        FABLE_RETENTION_ENSURED = True
        OPTED_IN_REGIONS.update(FABLE_BEDROCK_REGIONS)
        if bedrock_region:
            OPTED_IN_REGIONS.add(bedrock_region)
        return True

    regions = []
    for region in (bedrock_region, *FABLE_BEDROCK_REGIONS):
        if region not in regions:
            regions.append(region)

    all_success = True
    for region in regions:
        success, message = opt_in_provider_data_share(region=region)
        if success:
            if message:
                logger.info("Bedrock data retention opt-in: %s", message)
        else:
            logger.warning("Bedrock data retention opt-in failed: %s", message)
            all_success = False

    if all_success:
        FABLE_RETENTION_ENSURED = True
        _record_fable_opt_in(account_id)

    return all_success
