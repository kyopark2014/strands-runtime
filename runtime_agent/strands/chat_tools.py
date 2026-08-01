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

"""Tool result parsing and reference formatting for the chat module."""

from tool_parsers import (
    _build_tool_reference,
    _extract_rag_references_from_payload,
    is_tavily_content,
    parse_arxiv_result,
    parse_aws_read_documentation_result,
    parse_generic_result,
    parse_knowledge_base_result,
    parse_opensearch_result,
    parse_search_documentation_result,
    parse_tavily_result,
)


def _sanitize_reference_text(text: str, max_len: int) -> str:
    """Collapse whitespace/newlines and strip markdown that breaks list links."""
    if not text:
        return ""
    cleaned = " ".join(str(text).replace("\r", "\n").split())
    cleaned = cleaned.replace("```", "`").replace("[", "\\[").replace("]", "\\]")
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3].rstrip(" .") + "..."
    return cleaned


def _format_references_markdown(references: list) -> str:
    """Build a Reference section safe for markdown list rendering."""
    lines = ["\n\n### Reference"]
    for i, reference in enumerate(references, start=1):
        title = _sanitize_reference_text(reference.get("title") or "Untitled", 120)
        content = _sanitize_reference_text(reference.get("content") or "", 100)
        url = (reference.get("url") or "").strip()
        page = reference.get("page")
        page_suffix = f" , {page} page" if page is not None else ""
        if url:
            lines.append(
                f"{i}. [{title}]({url}){page_suffix} — {content}" if content
                else f"{i}. [{title}]({url}){page_suffix}"
            )
        else:
            lines.append(
                f"{i}. {title}{page_suffix} — {content}" if content
                else f"{i}. {title}{page_suffix}"
            )
    return "\n".join(lines) + "\n"


def get_tool_info(tool_name, tool_content):
    if is_tavily_content(tool_content):
        return parse_tavily_result(tool_content)
    if tool_name == "SearchIndexTool":
        return parse_opensearch_result(tool_content)
    if tool_name == "QueryKnowledgeBases":
        return parse_knowledge_base_result(tool_content)
    if tool_name == "search_documentation":
        return parse_search_documentation_result(tool_content)
    if tool_name == "search_papers" and "papers" in tool_content:
        return parse_arxiv_result(tool_content)
    if tool_name == "aws___read_documentation":
        return parse_aws_read_documentation_result(tool_content, tool_name)
    return parse_generic_result(tool_name, tool_content)
