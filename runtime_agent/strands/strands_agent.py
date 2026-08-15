"""Strands agent facade — public namespace preserved for agent.py and callers.

Implementation lives in focused modules:
  tools/          builtin @tool implementations
  mcp_manager.py  MCPClientManager + HTTP MCP helpers
  model_factory.py  get_model + Mantle/prompt-cache helpers
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

import chat
import utils
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
from strands import Agent, AgentSkills, Skill
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.session.file_session_manager import FileSessionManager
from strands_tools import current_time

from mcp_manager import MCPClientManager, init_mcp_clients, mcp_manager
from model_factory import (
    PROMPT_CACHE_TTL,
    REASONING_BUFFER_TOKENS,
    _build_mantle_openai_model,
    _ensure_mantle_base_url_patch,
    _log_prompt_cache_usage,
    _prompt_cache_kwargs,
    _supports_prompt_caching,
    get_model,
)
from tools import (
    ARTIFACTS_DIR,
    ARTIFACTS_REL,
    REPO_ROOT,
    SKILLS_DIR,
    WORKING_DIR,
    bash,
    ensure_user_skills_dir,
    execute_code,
    file_read,
    file_write,
    get_builtin_tools,
    get_user_skills_dir,
    memory_get,
    memory_search,
    resolve_workspace_path,
    s3_uri_to_console_url,
    upload_file_to_s3,
)

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("strands-agent")

strands_tools = []
mcp_servers = []

memory_id = actor_id = session_id = namespace = None

s3_prefix = "docs"
capture_prefix = "captures"

config = utils.load_config()
s3_bucket = config.get("s3_bucket")
sharing_url = config.get("sharing_url")

DEFAULT_CONVERSATION_WINDOW_SIZE = 50
ELLIPSIS_LEN = 3  # len("...")

BASE_SYSTEM_PROMPT = (
    "당신의 이름은 서연이고, 질문에 친근한 방식으로 대답하도록 설계된 대화형 AI입니다.\n"
    "상황에 맞는 구체적인 세부 정보를 충분히 제공합니다.\n"
    "모르는 질문을 받으면 솔직히 모른다고 말합니다.\n"
    "한국어로 답변하세요.\n"
    "답변 전에 개인 맥락이 필요하면 recall_memory(action=\"retrieve\", query=<사용자 질문>)를 "
    "1회 이상 호출하세요. 추측하지 말고 Memory에서 먼저 확인합니다.\n\n"
    "## Agent Workflow\n"
    "1. 사용자 입력을 받는다\n"
    "2. 개인 정보·선호·이전 맥락이 필요하면 recall_memory로 Memory를 조회한다\n"
    "3. 요청에 맞는 skill이 있으면 skills 도구로 해당 skill의 상세 지침을 로드한다\n"
    "4. skill 지침에 따라 file_read, file_write, execute_code, bash 등의 도구를 사용하여 작업을 수행한다\n"
    "5. file_write / file_read / execute_code / bash의 작업 디렉터리는 artifacts/이다. "
    "결과 파일은 파일명만으로 저장·참조한다 (예: report.docx, chart.png). "
    "application/artifacts/ 같은 경로는 자동으로 artifacts/로 매핑된다. "
    "skill 스크립트는 $WORKING_DIR/skills/... 절대 경로로 호출한다.\n"
    "6. 있으면 upload_file_to_s3로 업로드하여 URL을 제공한다\n"
    "7. 최종 결과를 사용자에게 전달한다\n"
)


def _skill_root_dirs() -> list[str]:
    """Builtin skills dir plus the current user's skills dir (if present)."""
    roots = [SKILLS_DIR]
    user_dir = get_user_skills_dir(getattr(chat, "user_id", None))
    if user_dir and os.path.isdir(user_dir) and os.path.normpath(user_dir) not in {
        os.path.normpath(r) for r in roots
    }:
        roots.append(user_dir)
    return roots


def _iter_skill_entries(skills_root: str) -> list[tuple[str, str]]:
    """Return (entry_name, skill_dir) pairs that contain SKILL.md."""
    result: list[tuple[str, str]] = []
    if not os.path.isdir(skills_root):
        return result
    try:
        entries = sorted(os.listdir(skills_root))
    except OSError as e:
        logger.warning("Failed to list skills directory %s: %s", skills_root, e)
        return result
    for entry in entries:
        skill_dir = os.path.join(skills_root, entry)
        if os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
            result.append((entry, skill_dir))
    return result


def available_skills() -> list[dict]:
    """Return name/description for skills under builtin + user skill dirs."""
    result = []
    seen: set[str] = set()
    for skills_root in _skill_root_dirs():
        for entry, skill_dir in _iter_skill_entries(skills_root):
            try:
                loaded = Skill.from_file(skill_dir)
                key = loaded.name or entry
                if key in seen:
                    continue
                seen.add(key)
                result.append({
                    "name": loaded.name,
                    "description": loaded.description,
                    "dir": entry,
                    "path": skill_dir,
                })
            except Exception as e:
                logger.warning(f"Failed to load skill '{entry}': {e}")
    return result


def resolve_skill_dir(skill_key: str) -> Optional[str]:
    """Map skill name (SKILL.md frontmatter) or directory name to skill path.

    Searches builtin ``SKILLS_DIR`` first, then the per-user skills directory.
    """
    if not skill_key:
        return None

    for skills_root in _skill_root_dirs():
        for entry, skill_dir in _iter_skill_entries(skills_root):
            if entry == skill_key:
                return skill_dir
            try:
                loaded = Skill.from_file(skill_dir)
                if loaded.name == skill_key:
                    return skill_dir
            except Exception as e:
                logger.warning(f"Failed to load skill '{entry}': {e}")

    logger.warning(f"Skill directory not found for key: {skill_key}")
    return None


def skill_dirs_from_list(skill_list: list[str]) -> list[str]:
    """Resolve UI/config skill keys to filesystem directories for AgentSkills."""
    dirs: list[str] = []
    seen: set[str] = set()
    for key in skill_list:
        path = resolve_skill_dir(key)
        if path:
            norm = os.path.normpath(path)
            if norm not in seen:
                dirs.append(path)
                seen.add(norm)
    return dirs


def _path_is_under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath(
            [os.path.normpath(path), os.path.normpath(root)]
        ) == os.path.normpath(root)
    except ValueError:
        return False


def agent_skills_sources(skill_list: list[str]) -> list[str]:
    """Build AgentSkills sources: selected skill dirs + per-user skills folder.

    Selected builtin skills are passed as individual directories. The per-user
    skills directory (``/mnt/workspace/{user-id}/skills``) is always appended
    as a parent source so skill-creator output is discoverable.
    """
    user_skills_dir = ensure_user_skills_dir(getattr(chat, "user_id", None))
    sources: list[str] = []
    seen: set[str] = set()

    for path in skill_dirs_from_list(skill_list):
        # Parent user-skills dir covers these; skip individuals under it.
        if _path_is_under(path, user_skills_dir):
            continue
        norm = os.path.normpath(path)
        if norm not in seen:
            sources.append(path)
            seen.add(norm)

    if os.path.isdir(user_skills_dir):
        norm_user = os.path.normpath(user_skills_dir)
        if norm_user not in seen:
            sources.append(user_skills_dir)
            seen.add(norm_user)

    return sources


conversation_manager = SlidingWindowConversationManager(
    window_size=DEFAULT_CONVERSATION_WINDOW_SIZE,
)


def _tool_name(tool_item) -> str:
    return tool_item.tool_name if hasattr(tool_item, "tool_name") else str(tool_item)


def update_tools(strands_tools: list, mcp_servers: list):
    # builtin tools
    tools = get_builtin_tools()
    # O(1) membership checks instead of scanning the tools list on every add.
    known_names = {_tool_name(t) for t in tools}

    tool_map = {
        "current_time": current_time,
        "file_read": file_read,
        "file_write": file_write
    }

    for tool_item in strands_tools:
        if isinstance(tool_item, str):
            if tool_item in tool_map:
                mapped = tool_map[tool_item]
                name = _tool_name(mapped)
                if name not in known_names:
                    tools.append(mapped)
                    known_names.add(name)
            else:
                logger.warning(f"Unknown string tool: {tool_item}")
            continue

        if isinstance(tool_item, list):
            for nested in tool_item:
                name = _tool_name(nested)
                if name not in known_names:
                    tools.append(nested)
                    known_names.add(name)
            continue

        name = _tool_name(tool_item)
        if hasattr(tool_item, "tool_name") and name in known_names:
            logger.info(f"builtin tool {name} already in tools")
            continue

        tools.append(tool_item)
        known_names.add(name)

    # MCP tools
    for mcp_tool in mcp_servers:
        logger.info(f"Processing MCP tool: {mcp_tool}")
        try:
            with mcp_manager.get_active_clients([mcp_tool]) as _:
                client = mcp_manager.get_client(mcp_tool)
                if client:
                    logger.info(f"Got client for {mcp_tool}, attempting to list tools...")
                    try:
                        mcp_servers_list = client.list_tools_sync()

                        if not mcp_servers_list:
                            logger.warning(f"No tools returned from {mcp_tool}")
                        else:
                            for mcp_server_item in mcp_servers_list:
                                name = mcp_server_item.tool_name
                                if name in known_names:
                                    logger.info(f"{name} already in tools")
                                    continue

                                tools.append(mcp_server_item)
                                known_names.add(name)
                                logger.info(f"Successfully added {name} from {mcp_tool} server")
                    except Exception as tool_error:
                        logger.error(f"Error listing tools for {mcp_tool}: {tool_error}")
                        continue
                else:
                    logger.error(f"Failed to get client for {mcp_tool}")
        except Exception as exc:
            logger.error(f"Error getting tools for {mcp_tool}: {exc}")
            logger.error(f"Exception type: {type(exc)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

    return tools


def get_runtime_session_id() -> str:
    runtime_session_id = BedrockAgentCoreContext.get_session_id()
    if not runtime_session_id:
        logger.warning("runtimeSessionId not found in request context; using 'default-session'")
        runtime_session_id = "default-session"
    return runtime_session_id


def append_tool_guidance_to_prompt(system_prompt: str, mcp_servers: list) -> str:
    """Append tool-specific usage guidance to the system prompt based on selected MCP servers.

    Add new guidance rules here as MCP servers are introduced.
    """
    if not mcp_servers:
        return system_prompt

    selected = {name.lower() for name in mcp_servers}
    extras: list[str] = []

    has_wiki = "wiki" in selected
    has_tavily_or_websearch = bool(selected & {"tavily", "websearch"})
    if has_wiki and has_tavily_or_websearch:
        extras.append("recall_wiki와 websearch tool을 이용해 병렬로 조회하세요.")

    if selected & {"aws documentation", "aws document"}:
        extras.append(
            "aws와 관련된 내용이 있다면, search_documentation tool을 이용해 필요한 정보를 수집하세요."
        )

    if not extras:
        return system_prompt

    logger.info(f"extra prompt: {extras}")

    return system_prompt + "\n" + "\n".join(extras)


def create_agent(strands_tools: list[str], mcp_servers: list[str], skill_list: list[str]):
    """Create Agent with Strands AgentSkills plugin for selected skills."""
    init_mcp_clients(mcp_servers)

    tools = update_tools(strands_tools, mcp_servers)

    model = get_model()

    skills_plugin = None
    if chat.skill_mode == "Enable" and skill_list:
        skills_sources = agent_skills_sources(skill_list)
        logger.info(f"skill_list: {skill_list} -> skills_sources: {skills_sources}")
        if skills_sources:
            skills_plugin = AgentSkills(skills=skills_sources)

    session_manager = FileSessionManager(
        session_id=get_runtime_session_id(),
        storage_dir="/mnt/workspace",
    )

    system_prompt = append_tool_guidance_to_prompt(BASE_SYSTEM_PROMPT, mcp_servers)

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        plugins=[skills_plugin] if skills_plugin else [],
        conversation_manager=conversation_manager,
        session_manager=session_manager,
    )

    return agent


def get_tool_list(tools):
    tool_list = []
    for tool in tools:
        if hasattr(tool, 'tool_name'):  # MCP tool
            tool_list.append(tool.tool_name)
                
        if str(tool).startswith("<module 'strands_tools."):   # strands_tools 
            module_name = str(tool).split("'")[1].split('.')[-1]
            tool_list.append(module_name)
    return tool_list


selected_strands_tools = []
selected_mcp_servers = []
selected_skill_list = []
selected_skill_mode = None
selected_session_id = None
selected_guardrail_enabled = None
selected_model_name = None
selected_user_id = None
agent = None

# OpenAI/Mantle reasoning and unsigned thinking cannot be replayed to Claude/Nova.
_BEDROCK_NON_REPLAYABLE_BLOCK_KEYS = frozenset({"reasoningContent", "reasoning", "thinking"})


def _is_non_replayable_content_block(block) -> bool:
    if not isinstance(block, dict):
        return False
    if any(key in block for key in _BEDROCK_NON_REPLAYABLE_BLOCK_KEYS):
        return True
    block_type = block.get("type")
    return block_type in ("reasoning", "thinking")


def _message_content_is_blank(content) -> bool:
    """True when content has no usable text and no tool blocks (Bedrock rejects blank text)."""
    if not isinstance(content, list) or not content:
        return True
    for block in content:
        if not isinstance(block, dict):
            return False
        if any(key in block for key in ("toolUse", "toolResult", "image", "document")):
            return False
        text = block.get("text")
        if text is not None and str(text).strip():
            return False
        # Non-text keys other than empty text mean the block is still meaningful.
        if any(key != "text" for key in block):
            return False
    return True


def sanitize_messages_for_bedrock_target(messages: list) -> list:
    """Strip reasoning/thinking blocks so GPT history can be replayed on Claude/Nova.

    Also drops messages whose content is entirely blank — Bedrock ConverseStream
    rejects ``messages[n].content[0].text == ""``.
    """
    sanitized = []
    for msg in messages:
        if not isinstance(msg, dict):
            sanitized.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            sanitized.append(msg)
            continue
        cleaned = [block for block in content if not _is_non_replayable_content_block(block)]
        if _message_content_is_blank(cleaned):
            # Drop blank turns rather than emitting {"text": ""}, which Bedrock rejects.
            continue
        new_msg = dict(msg)
        new_msg["content"] = cleaned
        sanitized.append(new_msg)
    return sanitized


def maybe_sanitize_agent_history_for_model() -> None:
    """When targeting Bedrock Claude/Nova, drop non-replayable/blank history in-place."""
    if agent is None:
        return
    messages = getattr(agent, "messages", None)
    if not messages:
        return
    if chat.model_type in ("claude", "nova"):
        cleaned = sanitize_messages_for_bedrock_target(list(messages))
    else:
        # Still drop blank text turns for other models — same Bedrock validation.
        cleaned = [
            msg
            for msg in messages
            if not (
                isinstance(msg, dict)
                and _message_content_is_blank(msg.get("content"))
            )
        ]
    if cleaned != list(messages):
        logger.info(
            "Sanitized history messages for model_type=%s (%s → %s)",
            chat.model_type,
            len(messages),
            len(cleaned),
        )
        agent.messages = cleaned


def _sanitize_reference_text(text: str, max_len: int) -> str:
    """Collapse whitespace/newlines and strip markdown that breaks list links."""
    if not text:
        return ""
    cleaned = " ".join(str(text).replace("\r", "\n").split())
    cleaned = cleaned.replace("```", "`").replace("[", "\\[").replace("]", "\\]")
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - ELLIPSIS_LEN].rstrip(" .") + "..."
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


def _agent_configuration_changed(
    strands_tools: list[str],
    mcp_servers: list[str],
    skill_list: list[str],
    current_skill_mode,
    current_session_id: str,
) -> bool:
    return (
        selected_strands_tools != strands_tools
        or selected_mcp_servers != mcp_servers
        or selected_skill_list != skill_list
        or selected_skill_mode != current_skill_mode
        or selected_session_id != current_session_id
        or selected_guardrail_enabled != chat.guardrail_enabled
        or selected_model_name != chat.model_name
        or selected_user_id != chat.user_id
        or agent is None
    )


def _refresh_agent_if_needed(
    strands_tools: list[str],
    mcp_servers: list[str],
    skill_list: list[str],
    current_skill_mode,
    current_session_id: str,
) -> None:
    global agent, selected_strands_tools, selected_mcp_servers, selected_skill_list
    global selected_skill_mode, selected_session_id, selected_guardrail_enabled
    global selected_model_name, selected_user_id

    if not _agent_configuration_changed(
        strands_tools, mcp_servers, skill_list, current_skill_mode, current_session_id
    ):
        return

    selected_strands_tools = list(strands_tools)
    selected_mcp_servers = list(mcp_servers)
    selected_skill_list = list(skill_list)
    selected_skill_mode = current_skill_mode
    selected_session_id = current_session_id
    selected_guardrail_enabled = chat.guardrail_enabled
    selected_model_name = chat.model_name
    selected_user_id = chat.user_id

    mcp_manager.stop_agent_clients()
    agent = create_agent(strands_tools, mcp_servers, skill_list)

    # Start or reuse persistent MCP clients for the refreshed agent.
    mcp_manager.start_agent_clients(mcp_servers)


def _extract_result_text(final) -> str:
    message = final.message
    if not message:
        return ""
    content = message.get("content", [])
    return content[0].get("text", "") if content else ""


def _publish_cloudwatch_token_metrics(final) -> None:
    try:
        import cloudwatch_metrics

        usage = cloudwatch_metrics.extract_token_usage(final)
        if not usage:
            metrics = getattr(final, "metrics", None)
            logger.warning(
                "Token usage missing on AgentResult; CloudWatch metrics skipped "
                "(model=%s accumulated_usage=%s)",
                chat.model_id,
                getattr(metrics, "accumulated_usage", None) if metrics else None,
            )
            return

        cloudwatch_metrics.publish_token_metrics(chat.model_id, final)
    except Exception as metric_err:
        logger.warning(f"CloudWatch token metrics publish skipped: {metric_err}")


def _collect_tool_result_artifacts(message: dict, queue, references: list, image_url: list) -> None:
    if "content" not in message:
        return

    msg_content = message["content"]
    logger.info(f"tool content: {msg_content}")
    for item in msg_content:
        if "toolResult" not in item:
            continue

        toolResult = item["toolResult"]
        toolUseId = toolResult["toolUseId"]
        toolContent = toolResult["content"]
        toolResultText = toolContent[0].get("text", "")
        tool_name = queue.get_tool_name(toolUseId)
        logger.info(f"[toolResult] {toolResultText}, [toolUseId] {toolUseId}")
        queue.notify(f"Tool Result: {str(toolResultText)}")

        info_content, urls, refs = chat.get_tool_info(tool_name, toolResultText)
        if refs:
            for reference in refs:
                references.append(reference)
            logger.info(f"refs: {refs}")
        if urls:
            for url in urls:
                image_url.append(url)
            logger.info(f"urls: {urls}")

        if info_content:
            logger.info(f"content: {info_content}")


async def _process_agent_stream(
    query: str,
    mcp_servers: list[str],
    queue,
    references: list,
    image_url: list,
    notification_queue,
) -> str:
    """Consume the agent event stream and publish the final result to the queue."""
    final_result = current = ""
    try:
        with mcp_manager.get_active_clients(mcp_servers) as _:
            agent_stream = agent.stream_async(query)

            async for event in agent_stream:
                text = ""
                if "data" in event:
                    text = event["data"]
                    logger.info(f"[data] {text}")
                    current += text
                    queue.stream(current)

                elif "result" in event:
                    final = event["result"]
                    final_result = _extract_result_text(final)
                    if final_result:
                        logger.info(f"[result] {final_result}")
                    _publish_cloudwatch_token_metrics(final)

                elif "current_tool_use" in event:
                    current_tool_use = event["current_tool_use"]
                    name = current_tool_use.get("name", "")
                    input_val = current_tool_use.get("input", "")
                    toolUseId = current_tool_use.get("toolUseId", "")

                    text = f"name: {name}, input: {input_val}"
                    logger.info(f"[current_tool_use] {text}")

                    queue.register_tool(toolUseId, name)
                    queue.tool_update(toolUseId, f"Tool: {name}, Input: {input_val}")
                    current = ""

                elif "message" in event:
                    message = event["message"]
                    logger.info(f"[message] {message}")
                    _collect_tool_result_artifacts(message, queue, references, image_url)

                elif "contentBlockDelta" or "contentBlockStop" or "messageStop" or "metadata" in event:
                    pass

                else:
                    logger.info(f"event: {event}")

            if references:
                final_result += _format_references_markdown(references)

            if notification_queue is not None:
                queue.result(final_result if final_result else current)
    except Exception:
        logger.exception("Agent stream processing failed")
        if notification_queue is not None:
            queue.notify("Agent processing failed. Please try again.")
        raise

    return final_result


async def run_strands_agent(query: str, strands_tools: list[str], mcp_servers: list[str], skill_list: list[str], notification_queue):
    """Run the strands agent with streaming and tool notifications."""
    queue = notification_queue
    queue.reset()

    image_url = []
    references = []

    current_skill_mode = chat.skill_mode
    current_session_id = get_runtime_session_id()
    _refresh_agent_if_needed(
        strands_tools, mcp_servers, skill_list, current_skill_mode, current_session_id
    )

    mcp_manager.start_agent_clients(mcp_servers)
    maybe_sanitize_agent_history_for_model()

    final_result = await _process_agent_stream(
        query, mcp_servers, queue, references, image_url, notification_queue
    )

    return final_result, image_url
