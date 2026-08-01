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

"""Shared boto3 client/resource factories with optional credential injection."""

from __future__ import annotations

import os
from typing import Any, Optional

import boto3


def env_credential_kwargs() -> dict[str, Any]:
    """Build boto3 credential kwargs from env vars, or {} for the default chain."""
    aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_session_token = os.environ.get("AWS_SESSION_TOKEN")
    if aws_access_key and aws_secret_key:
        return {
            "aws_access_key_id": aws_access_key,
            "aws_secret_access_key": aws_secret_key,
            "aws_session_token": aws_session_token,
        }
    return {}


def credential_kwargs(
    *,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    session_token: Optional[str] = None,
) -> dict[str, Any]:
    """Build boto3 credential kwargs from explicit values, else env, else {}."""
    if access_key and secret_key:
        return {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "aws_session_token": session_token,
        }
    return env_credential_kwargs()


def create_boto3_client(
    service_name: str,
    *,
    region_name: Optional[str] = None,
    config: Any = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    session_token: Optional[str] = None,
    **extra: Any,
):
    """Create a boto3 client with optional explicit/env credentials."""
    kwargs: dict[str, Any] = {**credential_kwargs(
        access_key=access_key,
        secret_key=secret_key,
        session_token=session_token,
    )}
    if region_name is not None:
        kwargs["region_name"] = region_name
    if config is not None:
        kwargs["config"] = config
    kwargs.update(extra)
    return boto3.client(service_name, **kwargs)


def create_boto3_resource(
    service_name: str,
    *,
    region_name: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    session_token: Optional[str] = None,
    **extra: Any,
):
    """Create a boto3 resource with optional explicit/env credentials."""
    kwargs: dict[str, Any] = {**credential_kwargs(
        access_key=access_key,
        secret_key=secret_key,
        session_token=session_token,
    )}
    if region_name is not None:
        kwargs["region_name"] = region_name
    kwargs.update(extra)
    return boto3.resource(service_name, **kwargs)
