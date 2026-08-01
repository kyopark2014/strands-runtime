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

"""MCP tools for Stable Diffusion 3.5 Large — thin handlers over image_generation_service."""

from __future__ import annotations

import logging
import sys
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

import image_generation_service as image_svc

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d | %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("sd35l-server")

# Ensure Bedrock client is ready at import (fail fast with a clear log).
try:
    image_svc.get_bedrock_client()
except Exception as e:
    logger.error("Failed to initialize Bedrock client for image generation: %s", e)
    raise

try:
    mcp = FastMCP(
        name="image_generation",
        instructions=(
            "You are a helpful assistant that generates images using Stable Diffusion 3.5 Large. "
            "You support text-to-image and image-to-image generation."
        ),
    )
    logger.info("MCP server initialized successfully")
except Exception as e:
    logger.error("MCP server init failed: %s", e)
    raise


@mcp.tool(name="generate_image")
async def generate_image(
    ctx: Context,
    prompt: str = Field(
        description="Text description of the image to generate. Use descriptive English captions with details about subject, environment, lighting, camera angle, and style."
    ),
    negative_prompt: Optional[str] = Field(
        default=None,
        description='Elements to exclude from the image (e.g., "blurry, low quality, distorted, bad anatomy")',
    ),
    aspect_ratio: str = Field(
        default="1:1",
        description="Output aspect ratio. Options: 1:1, 16:9, 9:16, 4:3, 3:4, 2:3, 3:2, 21:9, 9:21",
    ),
    seed: Optional[int] = Field(
        default=None,
        description="Seed for reproducibility (0-4294967294). Random if not specified.",
    ),
) -> dict:
    """Generate an image from text using Stable Diffusion 3.5 Large on Amazon Bedrock.

    SD3.5 Large delivers strong prompt adherence, photorealistic quality, and improved text rendering.

    ## Prompt Best Practices
    Structure prompts with: Subject, Environment, Pose/Action, Lighting, Camera angle, Style.
    Use descriptive captions rather than commands. Front-load important elements.
    Move negation words (no, not, without) to negative_prompt instead.

    ## Examples
    - "realistic editorial photo of female teacher standing at a blackboard, warm smile, soft natural lighting"
    - "drone view of a dark river winding through Iceland landscape, cinematic quality, dramatic clouds"
    - "watercolor illustration of a cozy cafe interior, warm tones, afternoon sunlight through windows"

    Returns:
        dict with status, paths (list of image URLs or local paths), and seed used.
    """
    try:
        return image_svc.generate_from_text(prompt, aspect_ratio, seed, negative_prompt)
    except Exception:
        logger.exception("generate_image tool failed")
        return {
            "status": "error",
            "error": "Image generation request failed",
            "path": [],
        }


@mcp.tool(name="generate_image_from_image")
async def generate_image_from_image(
    ctx: Context,
    prompt: str = Field(
        default="",
        description="Text description of the desired output image. (Required)",
    ),
    image_base64: str = Field(
        default="",
        description="Base64-encoded source image for style transfer or variation. (Required)",
    ),
    strength: float = Field(
        default=0.7,
        description="How much to transform the source image. 0.0=no change, 1.0=completely new. Recommended: 0.3 subtle, 0.5 moderate, 0.7 major, 1.0 full.",
    ),
    negative_prompt: Optional[str] = Field(
        default=None,
        description="Elements to exclude from the image.",
    ),
    seed: Optional[int] = Field(
        default=None,
        description="Seed for reproducibility (0-4294967294).",
    ),
) -> dict:
    """Transform an existing image using Stable Diffusion 3.5 Large (image-to-image).

    Use this for style transfer, variations, or guided modifications of an existing image.
    The strength parameter controls how much the output differs from the input.

    Returns:
        dict with status, paths (list of image URLs or local paths), and seed used.
    """
    try:
        return image_svc.generate_from_image(
            prompt, image_base64, strength, seed, negative_prompt
        )
    except Exception:
        logger.exception("generate_image_from_image tool failed")
        return {
            "status": "error",
            "error": "Image generation request failed",
            "path": [],
        }


if __name__ == "__main__":
    mcp.run(transport="stdio")
