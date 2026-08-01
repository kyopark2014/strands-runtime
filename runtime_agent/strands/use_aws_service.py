"""Service layer for the use_aws MCP tool (validation, client, execution)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ParamValidationError, ValidationError
from botocore.response import StreamingBody

import use_aws as aws_utils

logger = logging.getLogger("mcp-server-aws-cost")

MUTATIVE_OPERATIONS = [
    "create",
    "put",
    "delete",
    "update",
    "terminate",
    "revoke",
    "disable",
    "deregister",
    "stop",
    "add",
    "modify",
    "remove",
    "attach",
    "detach",
    "start",
    "enable",
    "register",
    "set",
    "associate",
    "disassociate",
    "allocate",
    "release",
    "cancel",
    "reboot",
    "accept",
]


def _aws_credentials() -> Dict[str, Optional[str]]:
    cfg = aws_utils.config
    return {
        "access_key": cfg.get("aws", {}).get("access_key_id"),
        "secret_key": cfg.get("aws", {}).get("secret_access_key"),
        "session_token": cfg.get("aws", {}).get("session_token"),
        "region": cfg.get("region", "us-west-2"),
    }


def default_region() -> str:
    return _aws_credentials()["region"] or "us-west-2"


def get_boto3_client(
    service_name: str,
    region_name: str,
    profile_name: Optional[str] = None,
) -> Any:
    """Create an AWS boto3 client for the specified service and region."""
    creds = _aws_credentials()
    if creds["access_key"] and creds["secret_key"]:
        session = boto3.Session(
            profile_name=profile_name,
            aws_access_key_id=creds["access_key"],
            aws_secret_access_key=creds["secret_key"],
            aws_session_token=creds["session_token"],
        )
    else:
        session = boto3.Session(
            profile_name=profile_name,
            region_name=region_name,
        )
    return session.client(service_name=service_name, region_name=region_name)


def handle_streaming_body(response: Dict[str, Any]) -> Dict[str, Any]:
    """Convert StreamingBody values in an AWS response into JSON/text."""
    for key, value in response.items():
        if isinstance(value, StreamingBody):
            content = value.read()
            try:
                response[key] = json.loads(content.decode("utf-8"))
            except json.JSONDecodeError:
                response[key] = content.decode("utf-8")
    return response


def get_available_services() -> List[str]:
    return list(boto3.Session().get_available_services())


def get_available_operations(service_name: str) -> List[str]:
    region = os.environ.get("AWS_REGION", default_region())
    creds = _aws_credentials()
    try:
        if creds["access_key"] and creds["secret_key"]:
            client = boto3.client(
                service_name,
                region_name=region,
                aws_access_key_id=creds["access_key"],
                aws_secret_access_key=creds["secret_key"],
                aws_session_token=creds["session_token"],
            )
        else:
            client = boto3.client(service_name, region_name=region)
        return [op for op in dir(client) if not op.startswith("_")]
    except Exception as exc:
        logger.error("Error getting operations for service %s: %s", service_name, exc)
        return []


def validate_service(service_name: str) -> Optional[Dict[str, Any]]:
    """Return an error tool result if service_name is invalid, else None."""
    available_services = get_available_services()
    logger.info("Available services: %s", available_services)
    if service_name in available_services:
        return None
    logger.debug("Invalid AWS service: %s", service_name)
    return {
        "status": "error",
        "content": [
            {
                "text": (
                    f"Invalid AWS service: {service_name}\n"
                    f"Available services: {str(available_services)}"
                )
            }
        ],
    }


def validate_operation(service_name: str, operation_name: str) -> Optional[Dict[str, Any]]:
    """Return an error tool result if operation_name is invalid, else None."""
    available_operations = get_available_operations(service_name)
    if operation_name in available_operations:
        return None
    logger.debug("Invalid AWS operation: %s", operation_name)
    return {
        "status": "error",
        "content": [
            {
                "text": (
                    f"Invalid AWS operation: {operation_name}, "
                    f"Available operations:\n{available_operations}\n"
                )
            }
        ],
    }


def _format_aws_success(response: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "content": [{"text": f"Success: {str(response)}"}],
    }


def _format_parameter_validation_error(
    service_name: str,
    operation_name: str,
    val_ex: Exception,
) -> Dict[str, Any]:
    """Build a ToolResult for boto3 parameter validation failures."""
    logger.error(
        "Parameter validation failed for %s.%s: %s",
        service_name,
        operation_name,
        val_ex,
        exc_info=True,
    )
    client_msg = (
        f"Invalid parameters provided for operation '{operation_name}' "
        f"on service '{service_name}'."
    )
    try:
        schema = aws_utils.generate_input_schema(service_name, operation_name)
        logger.info("Schema: %s", schema)
        return {
            "status": "error",
            "content": [
                {"text": client_msg},
                {"text": f"Expected input schema for {operation_name}:"},
                {"text": json.dumps(schema, indent=2)},
            ],
        }
    except Exception as schema_ex:
        logger.error("Failed to generate schema: %s", schema_ex, exc_info=True)
        return {
            "status": "error",
            "content": [{"text": client_msg}],
        }


def _format_aws_operation_failure(
    service_name: str,
    operation_name: str,
    ex: Exception,
) -> Dict[str, Any]:
    logger.warning(
        "AWS call %s.%s threw %s: %s",
        service_name,
        operation_name,
        type(ex).__name__,
        ex,
        exc_info=True,
    )
    return {
        "status": "error",
        "content": [{"text": "AWS operation failed"}],
    }


def execute_aws_operation(
    service_name: str,
    operation_name: str,
    parameters: Dict[str, Any],
    region: str,
    profile_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a boto3 operation and return a ToolResult-shaped dict."""
    client = get_boto3_client(service_name, region, profile_name)
    operation_method = getattr(client, operation_name)

    try:
        response = operation_method(**parameters)
        response = handle_streaming_body(response)
        response = aws_utils.convert_datetime_to_str(response)
        return _format_aws_success(response)
    except (ValidationError, ParamValidationError) as val_ex:
        return _format_parameter_validation_error(service_name, operation_name, val_ex)
    except Exception as ex:
        return _format_aws_operation_failure(service_name, operation_name, ex)


def run_use_aws(
    service_name: str,
    operation_name: str,
    parameters: Dict[str, Any],
    region: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate inputs and execute the AWS operation (MCP-agnostic entrypoint)."""
    if region is None:
        region = default_region()

    logger.debug(
        "Invoking: service_name=%s, operation_name=%s, parameters=%s",
        service_name,
        operation_name,
        parameters,
    )

    service_error = validate_service(service_name)
    if service_error:
        return service_error

    operation_error = validate_operation(service_name, operation_name)
    if operation_error:
        return operation_error

    return execute_aws_operation(
        service_name=service_name,
        operation_name=operation_name,
        parameters=parameters,
        region=region,
        profile_name=profile_name,
    )
