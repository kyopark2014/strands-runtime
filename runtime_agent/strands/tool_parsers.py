"""Tool-specific result parsers extracted from chat_tools.get_tool_info."""

import json
import logging

logger = logging.getLogger("chat")


def _build_tool_reference(ref_item: dict) -> dict:
    """Build a display reference from a RAG doc item."""
    reference = ref_item.get("reference") or {}
    contents = ref_item.get("contents") or ""
    content_text = contents[:100] + "..." if len(contents) > 100 else contents
    result = {
        "url": reference.get("url"),
        "title": reference.get("title"),
        "content": content_text,
    }
    if reference.get("page") is not None:
        result["page"] = reference["page"]
    return result


def _extract_rag_references_from_payload(json_data) -> list:
    """Extract RAG {contents, reference} items from MCP tool payloads."""
    refs = []
    if isinstance(json_data, dict):
        if "reference" in json_data and "contents" in json_data:
            refs.append(_build_tool_reference(json_data))
            return refs
        for value in json_data.values():
            if isinstance(value, dict) and "reference" in value and "contents" in value:
                refs.append(_build_tool_reference(value))
        return refs

    if not isinstance(json_data, list):
        return refs

    for item in json_data:
        if not isinstance(item, dict):
            continue
        if "text" in item and ("type" not in item or item.get("type") == "text"):
            text_val = item.get("text")
            if isinstance(text_val, str):
                try:
                    text_json = json.loads(text_val)
                except (json.JSONDecodeError, TypeError) as e:
                    preview = text_val[:120] if isinstance(text_val, str) else repr(text_val)
                    logger.warning(
                        "Skipping malformed RAG tool payload: %s; preview=%r",
                        e,
                        preview,
                    )
                    continue
                refs.extend(_extract_rag_references_from_payload(text_json))
            continue
        if "reference" in item and "contents" in item:
            refs.append(_build_tool_reference(item))
    return refs


def is_tavily_content(tool_content) -> bool:
    """Return True when tool_content matches Tavily search result format."""
    return (
        isinstance(tool_content, str)
        and "Title:" in tool_content
        and "URL:" in tool_content
        and "Content:" in tool_content
    )


def parse_tavily_result(tool_content: str) -> tuple[str, list, list]:
    """Parse Tavily search results formatted as Title/URL/Content blocks."""
    tool_references = []
    urls = []
    content = ""

    logger.info("Tavily parsing...")
    items = tool_content.split("\n\n")
    for i, item in enumerate(items):
        # logger.info(f"item[{i}]: {item}")
        if "Title:" in item and "URL:" in item and "Content:" in item:
            try:
                title_part = item.split("Title:")[1].split("URL:")[0].strip()
                url_part = item.split("URL:")[1].split("Content:")[0].strip()
                content_part = item.split("Content:")[1].strip().replace("\n", "")

                logger.info(f"title_part: {title_part}")
                logger.info(f"url_part: {url_part}")
                logger.info(f"content_part: {content_part}")

                content += f"{content_part}\n\n"

                tool_references.append({
                    "url": url_part,
                    "title": title_part,
                    "content": content_part[:100] + "..." if len(content_part) > 100 else content_part
                })
            except Exception as e:
                logger.info(f"Parsing error: {str(e)}")
                continue

    return content, urls, tool_references


def parse_opensearch_result(tool_content) -> tuple[str, list, list]:
    """Parse SearchIndexTool / OpenSearch hit payloads."""
    tool_references = []
    urls = []
    content = ""

    if ":" in tool_content:
        extracted_json_data = tool_content.split(":", 1)[1].strip()
        try:
            json_data = json.loads(extracted_json_data)
            # logger.info(f"extracted_json_data: {extracted_json_data[:200]}")
        except json.JSONDecodeError:
            logger.info("JSON parsing error")
            json_data = {}
    else:
        json_data = {}

    if "hits" in json_data:
        hits = json_data["hits"]["hits"]
        if hits:
            logger.info(f"hits[0]: {hits[0]}")

        for hit in hits:
            text = hit["_source"]["text"]
            metadata = hit["_source"]["metadata"]

            content += f"{text}\n\n"

            filename = metadata["name"].split("/")[-1]
            # logger.info(f"filename: {filename}")

            content_part = text.replace("\n", "")
            tool_references.append({
                "url": metadata["url"],
                "title": filename,
                "content": content_part[:100] + "..." if len(content_part) > 100 else content_part
            })

    logger.info(f"content: {content}")
    return content, urls, tool_references


def parse_knowledge_base_result(tool_content) -> tuple[str, list, list]:
    """Parse QueryKnowledgeBases tool results."""
    import chat

    tool_references = []
    urls = []
    content = ""

    try:
        # Handle case where tool_content contains multiple JSON objects
        if tool_content.strip().startswith('{'):
            # Parse each JSON object individually
            json_objects = []
            current_pos = 0
            brace_count = 0
            start_pos = -1

            for i, char in enumerate(tool_content):
                if char == '{':
                    if brace_count == 0:
                        start_pos = i
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0 and start_pos != -1:
                        try:
                            json_obj = json.loads(tool_content[start_pos:i+1])
                            # logger.info(f"json_obj: {json_obj}")
                            json_objects.append(json_obj)
                        except json.JSONDecodeError:
                            logger.info(f"JSON parsing error: {tool_content[start_pos:i+1][:100]}")
                        start_pos = -1

            json_data = json_objects
        else:
            # Try original method
            json_data = json.loads(tool_content)
        # logger.info(f"json_data: {json_data}")

        # Build content
        if isinstance(json_data, list):
            for item in json_data:
                if isinstance(item, dict) and "content" in item:
                    content_text = item["content"].get("text", "")
                    content += content_text + "\n\n"

                    uri = ""
                    if "location" in item:
                        if "s3Location" in item["location"]:
                            uri = item["location"]["s3Location"]["uri"]
                            # logger.info(f"uri (list): {uri}")
                            ext = uri.split(".")[-1]

                            # if ext is an image
                            url = chat.sharing_url + "/" + chat.s3_prefix + "/" + uri.split("/")[-1]
                            if ext in ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "ico", "webp"]:
                                url = chat.sharing_url + "/" + chat.capture_prefix + "/" + uri.split("/")[-1]
                            logger.info(f"url: {url}")

                            tool_references.append({
                                "url": url,
                                "title": uri.split("/")[-1],
                                "content": content_text[:100] + "..." if len(content_text) > 100 else content_text
                            })

    except json.JSONDecodeError as e:
        logger.info(f"JSON parsing error: {e}")
        content = tool_content  # Use original content if parsing fails

    logger.info(f"content: {content}")
    logger.info(f"tool_references: {tool_references}")
    return content, urls, tool_references


def parse_search_documentation_result(tool_content) -> tuple[str, list, list]:
    """Parse AWS search_documentation tool results."""
    tool_references = []
    urls = []
    content = ""

    try:
        # Handle case where tool_content is a list (e.g., [{'type': 'text', 'text': '...'}])
        if isinstance(tool_content, list):
            # Extract text field from the first item in the list
            if len(tool_content) > 0 and isinstance(tool_content[0], dict) and 'text' in tool_content[0]:
                tool_content = tool_content[0]['text']
            else:
                logger.info(f"Unexpected list format: {tool_content}")
                return content, urls, tool_references

        # Parse JSON if tool_content is a string
        if isinstance(tool_content, str):
            json_data = json.loads(tool_content)
        elif isinstance(tool_content, dict):
            json_data = tool_content
        else:
            logger.info(f"Unexpected tool_content type: {type(tool_content)}")
            return content, urls, tool_references

        # Extract results from search_results array
        search_results = json_data.get('search_results', [])
        if not search_results:
            # If search_results is not found, json_data itself may be an array
            if isinstance(json_data, list):
                search_results = json_data
            else:
                logger.info(f"No search_results found in JSON data")
                return content, urls, tool_references

        for item in search_results:
            logger.info(f"item: {item}")

            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except json.JSONDecodeError:
                    logger.info(f"Failed to parse item as JSON: {item}")
                    continue

            if isinstance(item, dict) and 'url' in item and 'title' in item:
                url = item['url']
                title = item['title']
                content_text = item.get('context', '')[:100] + "..." if len(item.get('context', '')) > 100 else item.get('context', '')
                tool_references.append({
                    "url": url,
                    "title": title,
                    "content": content_text
                })
            else:
                logger.info(f"Invalid item format: {item}")

    except json.JSONDecodeError as e:
        logger.info(f"JSON parsing error: {e}, tool_content: {tool_content}")
        pass
    except Exception as e:
        logger.info(f"Unexpected error in search_documentation: {e}, tool_content type: {type(tool_content)}")
        pass

    logger.info(f"content: {content}")
    logger.info(f"tool_references: {tool_references}")
    return content, urls, tool_references


def parse_arxiv_result(tool_content) -> tuple[str, list, list]:
    """Parse ArXiv search_papers tool results."""
    tool_references = []
    urls = []
    content = ""

    try:
        json_data = json.loads(tool_content)

        papers = json_data['papers']
        for paper in papers:
            url = paper['url']
            title = paper['title']
            abstract = paper['abstract'].replace("\n", "")
            content_text = abstract[:100] + "..." if len(abstract) > 100 else abstract
            content += f"{content_text}\n\n"
            logger.info(f"url: {url}, title: {title}, content: {content_text}")

            tool_references.append({
                "url": url,
                "title": title,
                "content": content_text
            })
    except json.JSONDecodeError:
        logger.info(f"JSON parsing error: {tool_content}")
        pass

    logger.info(f"content: {content}")
    logger.info(f"tool_references: {tool_references}")
    return content, urls, tool_references


def parse_aws_read_documentation_result(tool_content, tool_name: str) -> tuple[str, list, list]:
    """Parse aws___read_documentation tool results."""
    tool_references = []
    urls = []
    content = ""

    logger.info(f"#### {tool_name} ####")
    if isinstance(tool_content, dict):
        json_data = tool_content
    elif isinstance(tool_content, list):
        json_data = tool_content
    else:
        json_data = json.loads(tool_content)

    logger.info(f"json_data: {json_data}")

    if "content" in json_data:
        content = json_data["content"]
        logger.info(f"content: {content}")
        if "result" in content:
            result = content["result"]
            logger.info(f"result: {result}")

    payload = {}
    if "response" in json_data:
        payload = json_data["response"]["payload"]
    elif "content" in json_data:
        payload = json_data

    if "content" in payload:
        payload_content = payload["content"]
        if "result" in payload_content:
            result = payload_content["result"]
            logger.info(f"result: {result}")
            if isinstance(result, str) and "AWS Documentation from" in result:
                logger.info(f"Processing AWS Documentation format: {result}")
                try:
                    # Extract URL from "AWS Documentation from https://..."
                    url_start = result.find("https://")
                    if url_start != -1:
                        # Find the colon after the URL (not inside the URL)
                        url_end = result.find(":", url_start)
                        if url_end != -1:
                            # Check if the colon is part of the URL or the separator
                            url_part = result[url_start:url_end]
                            # If the colon is immediately after the URL, use it as separator
                            if result[url_end:url_end+2] == ":\n":
                                url = url_part
                                content_start = url_end + 2  # Skip the colon and newline
                            else:
                                # Try to find the actual URL end by looking for space or newline
                                space_pos = result.find(" ", url_start)
                                newline_pos = result.find("\n", url_start)
                                if space_pos != -1 and newline_pos != -1:
                                    url_end = min(space_pos, newline_pos)
                                elif space_pos != -1:
                                    url_end = space_pos
                                elif newline_pos != -1:
                                    url_end = newline_pos
                                else:
                                    url_end = len(result)

                                url = result[url_start:url_end]
                                content_start = url_end + 1

                            # Remove trailing colon from URL if present
                            if url.endswith(":"):
                                url = url[:-1]

                            # Extract content after the URL
                            if content_start < len(result):
                                content_text = result[content_start:].strip()
                                # Truncate content for display
                                display_content = content_text[:100] + "..." if len(content_text) > 100 else content_text
                                display_content = display_content.replace("\n", "")

                                tool_references.append({
                                    "url": url,
                                    "title": "AWS Documentation",
                                    "content": display_content
                                })
                                content += content_text + "\n\n"
                                logger.info(f"Extracted URL: {url}")
                                logger.info(f"Extracted content length: {len(content_text)}")
                except Exception as e:
                    logger.error(f"Error parsing AWS Documentation format: {e}")
    logger.info(f"content: {content}")
    logger.info(f"tool_references: {tool_references}")
    return content, urls, tool_references


def parse_generic_result(tool_name, tool_content) -> tuple[str, list, list]:
    """Parse generic tool results including RAG JSON payloads and path URLs."""
    tool_references = []
    urls = []
    content = ""

    try:
        if isinstance(tool_content, dict):
            json_data = tool_content
        elif isinstance(tool_content, list):
            json_data = tool_content
        else:
            json_data = json.loads(tool_content)

        logger.info(f"json_data: {json_data}")
        if isinstance(json_data, dict) and "path" in json_data:  # path
            path = json_data["path"]
            if isinstance(path, list):
                for url in path:
                    urls.append(url)
            else:
                urls.append(path)

        # RAG retrieve returns MCP blocks: [{"type":"text","text":"[{contents,reference}...]"}]
        extracted = _extract_rag_references_from_payload(json_data)
        tool_references.extend(extracted)

        logger.info(f"tool_references: {tool_references}")

    except json.JSONDecodeError as e:
        logger.warning(
            "Failed to parse tool result as JSON for tool=%s: %s; content_preview=%r",
            tool_name,
            e,
            (tool_content[:200] if isinstance(tool_content, str) else type(tool_content).__name__),
        )

    return content, urls, tool_references
