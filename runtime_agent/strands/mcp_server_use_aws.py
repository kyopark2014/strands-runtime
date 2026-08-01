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

"""MCP server entrypoint for the use_aws tool. Business logic lives in use_aws_service."""

import logging
import sys
from typing import Any, Dict, Optional

from colorama import Fore, Style
from mcp.server.fastmcp import FastMCP
from rich.panel import Panel

import use_aws as aws_utils
import use_aws_service

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d | %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mcp-server-aws-cost")

try:
    mcp = FastMCP(
        name="tools",
        instructions=(
            "You are a helpful assistant. "
            "You can check the status of Amazon S3 and retrieve insights."
        ),
    )
    logger.info("MCP server initialized successfully")
except Exception as e:
    logger.exception("MCP server initialization failed: %s", type(e).__name__)

TOOL_SPEC = {
    "name": "use_aws",
    "description": (
        "Make a boto3 client call with the specified service, operation, and parameters. "
        "Boto3 operations are snake_case."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "description": "The name of the AWS service",
                },
                "operation_name": {
                    "type": "string",
                    "description": "The name of the operation to perform",
                },
                "parameters": {
                    "type": "object",
                    "description": "The parameters for the operation",
                },
                "region": {
                    "type": "string",
                    "description": "Region name for calling the operation on AWS boto3",
                },
                "label": {
                    "type": "string",
                    "description": (
                        "Label of AWS API operations human readable explanation. "
                        "This is useful for communicating with human."
                    ),
                },
                "profile_name": {
                    "type": "string",
                    "description": (
                        "Optional: AWS profile name to use from ~/.aws/credentials. "
                        "Defaults to default profile if not specified."
                    ),
                },
            },
            "required": [
                "region",
                "service_name",
                "operation_name",
                "parameters",
                "label",
            ],
        }
    },
}


def _format_operation_panel(
    service_name: str,
    operation_name: str,
    parameters: Dict[str, Any],
    label: str,
) -> None:
    console = aws_utils.create()
    operation_details = f"{Fore.CYAN}Service:{Style.RESET_ALL} {service_name}\n"
    operation_details += f"{Fore.CYAN}Operation:{Style.RESET_ALL} {operation_name}\n"
    operation_details += f"{Fore.CYAN}Parameters:{Style.RESET_ALL}\n"
    for key, value in parameters.items():
        operation_details += f"  - {key}: {value}\n"
    console.print(Panel(operation_details, title=label, expand=False))


@mcp.tool()
def use_aws(
    service_name: str,
    operation_name: str,
    parameters: Dict[str, Any],
    region: Optional[str] = None,
    label: str = "AWS Operation Details",
    profile_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute AWS service operations using boto3 with comprehensive error handling and validation.

    This tool provides a universal interface to AWS services, allowing you to execute
    any operation supported by boto3. It handles authentication, parameter validation,
    response formatting, and provides helpful error messages with schema recommendations
    when invalid parameters are provided.

    Args:
        service_name: AWS service name (e.g., 's3', 'ec2', 'dynamodb')
        operation_name: Operation to perform in snake_case (e.g., 'list_buckets')
        parameters: Dictionary of parameters for the operation
        region: AWS region (e.g., 'us-west-2')
        label: Human-readable description of the operation
        profile_name: Optional AWS profile name for credentials

    Returns:
        ToolResult dictionary with status ('success' or 'error') and content list.
    """
    _format_operation_panel(service_name, operation_name, parameters, label)
    try:
        return use_aws_service.run_use_aws(
            service_name=service_name,
            operation_name=operation_name,
            parameters=parameters,
            region=region,
            profile_name=profile_name,
        )
    except Exception:
        logger.exception(
            "use_aws failed for %s.%s",
            service_name,
            operation_name,
        )
        return {
            "status": "error",
            "content": [
                {
                    "type": "text",
                    "text": "AWS operation failed. Check server logs for details.",
                }
            ],
        }


if __name__ == "__main__":
    mcp.run(transport="stdio")
