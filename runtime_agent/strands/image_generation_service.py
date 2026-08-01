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

"""Stable Diffusion 3.5 Large image generation business logic for MCP tools."""

from __future__ import annotations

import base64
import json
import logging
import os
import random
from typing import Any, Optional

import boto3
from botocore.config import Config

logger = logging.getLogger("image_generation_service")

MODEL_ID = "stability.sd3-5-large-v1:0"
AWS_REGION = "us-west-2"
VALID_ASPECT_RATIOS = [
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "2:3",
    "3:2",
    "21:9",
    "9:21",
]
MAX_SEED = 4294967294
WORKING_DIR = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS_DIR = os.path.join(WORKING_DIR, "artifacts")

_bedrock_client = None


def get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is not None:
        return _bedrock_client
    cfg = Config(read_timeout=120, retries={"max_attempts": 2})
    if aws_profile := os.environ.get("AWS_PROFILE"):
        _bedrock_client = boto3.Session(
            profile_name=aws_profile, region_name=AWS_REGION
        ).client("bedrock-runtime", config=cfg)
    else:
        _bedrock_client = boto3.Session(region_name=AWS_REGION).client(
            "bedrock-runtime", config=cfg
        )
    return _bedrock_client


def _has_sharing_url() -> bool:
    try:
        import utils

        cfg = utils.load_config()
        return bool(cfg.get("sharing_url"))
    except Exception:
        return False


def _upload_to_s3(image_bytes: bytes, filename: str) -> Optional[str]:
    try:
        import chat

        url = chat.upload_to_s3(image_bytes, filename)
        if url and url.startswith("http"):
            logger.info("Uploaded to S3: %s", url)
            return url
    except ImportError:
        logger.warning("chat module not available, skipping S3 upload")
    except Exception as e:
        logger.error("S3 upload failed: %s", e)
    return None


def invoke_sd35(request_body: dict) -> dict:
    response = get_bedrock_client().invoke_model(
        modelId=MODEL_ID,
        body=json.dumps(request_body),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(response["body"].read())


def process_generation_result(result: dict, prefix: str = "sd35l") -> dict:
    finish_reasons = result.get("finish_reasons", [])
    if finish_reasons and finish_reasons[0] == "CONTENT_FILTERED":
        return {
            "status": "error",
            "error": "Content was filtered by the safety system. Please revise your prompt.",
            "path": [],
        }

    images = result.get("images", [])
    seeds = result.get("seeds", [])
    if not images:
        return {
            "status": "error",
            "error": "No images returned from the model.",
            "path": [],
        }

    use_s3 = _has_sharing_url()
    paths = []
    for i, img_b64 in enumerate(images):
        image_bytes = base64.b64decode(img_b64)
        rand_id = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=8))
        filename = f"{prefix}_{rand_id}.png"

        if use_s3:
            url = _upload_to_s3(image_bytes, filename)
            if url:
                paths.append(url)
                continue

        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        local_path = os.path.join(ARTIFACTS_DIR, filename)
        with open(local_path, "wb") as f:
            f.write(image_bytes)
        paths.append(local_path)
        logger.info("Saved to artifacts: %s", local_path)

    return {
        "status": "success",
        "path": paths,
        "seed": seeds[0] if seeds else None,
    }


def prepare_text_to_image_request(
    prompt: str,
    aspect_ratio: str = "1:1",
    seed: Optional[int] = None,
    negative_prompt: Optional[str] = None,
) -> dict[str, Any]:
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        raise ValueError(
            f"Invalid aspect_ratio '{aspect_ratio}'. Valid: {VALID_ASPECT_RATIOS}"
        )
    actual_seed = seed if seed is not None else random.randint(0, MAX_SEED)
    request_body: dict[str, Any] = {
        "prompt": prompt,
        "mode": "text-to-image",
        "aspect_ratio": aspect_ratio,
        "seed": actual_seed,
        "output_format": "png",
    }
    if negative_prompt:
        request_body["negative_prompt"] = negative_prompt
    return request_body


def prepare_image_to_image_request(
    prompt: str,
    image_base64: str,
    strength: float = 0.7,
    seed: Optional[int] = None,
    negative_prompt: Optional[str] = None,
) -> dict[str, Any]:
    if not prompt:
        raise ValueError("prompt is required.")
    if not image_base64:
        raise ValueError("image_base64 is required.")
    strength = max(0.0, min(1.0, strength))
    actual_seed = seed if seed is not None else random.randint(0, MAX_SEED)
    request_body: dict[str, Any] = {
        "prompt": prompt,
        "mode": "image-to-image",
        "image": image_base64,
        "strength": strength,
        "seed": actual_seed,
        "output_format": "png",
    }
    if negative_prompt:
        request_body["negative_prompt"] = negative_prompt
    return request_body


def generate_from_text(
    prompt: str,
    aspect_ratio: str = "1:1",
    seed: Optional[int] = None,
    negative_prompt: Optional[str] = None,
) -> dict:
    try:
        request_body = prepare_text_to_image_request(
            prompt, aspect_ratio, seed, negative_prompt
        )
    except ValueError as e:
        logger.warning("Invalid image generation request: %s", e)
        return {"status": "error", "error": "Invalid image generation request", "path": []}
    logger.info(
        "generate_image: prompt=%r..., aspect_ratio=%s, seed=%s",
        prompt[:60],
        aspect_ratio,
        request_body["seed"],
    )
    try:
        result = invoke_sd35(request_body)
        return process_generation_result(result)
    except Exception:
        logger.exception("Image generation failed")
        return {
            "status": "error",
            "error": "Image generation request failed",
            "path": [],
        }


def generate_from_image(
    prompt: str,
    image_base64: str,
    strength: float = 0.7,
    seed: Optional[int] = None,
    negative_prompt: Optional[str] = None,
) -> dict:
    try:
        request_body = prepare_image_to_image_request(
            prompt, image_base64, strength, seed, negative_prompt
        )
    except ValueError as e:
        logger.warning("Invalid image-to-image request: %s", e)
        return {"status": "error", "error": "Invalid image generation request", "path": []}
    logger.info(
        "generate_image_from_image: prompt=%r..., strength=%s, seed=%s",
        prompt[:60],
        request_body["strength"],
        request_body["seed"],
    )
    try:
        result = invoke_sd35(request_body)
        return process_generation_result(result, prefix="sd35l_i2i")
    except Exception:
        logger.exception("Image-to-image generation failed")
        return {
            "status": "error",
            "error": "Image generation request failed",
            "path": [],
        }
