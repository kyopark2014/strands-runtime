"""AgentCore Memory strategy definitions and extraction prompts."""

from __future__ import annotations

# Must be an inference profile available in the configured region (us-west-2).
MEMORY_EXTRACTION_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

USER_PREFERENCE_STRATEGY_NAME = "UserPreference"
USER_PREFERENCE_NAMESPACE_TEMPLATE = "/users/{actorId}/preferences"
SUMMARY_STRATEGY_NAME = "Summary"
SUMMARY_NAMESPACE_TEMPLATE = "/users/{actorId}/sessions/{sessionId}"
SEMANTIC_STRATEGY_NAME = "Semantic"
SEMANTIC_NAMESPACE_TEMPLATE = "/users/{actorId}/facts"

USER_PREFERENCE_PROMPT = (
    "You are tasked with analyzing conversations to extract the user's preferences. You'll be analyzing two sets of data:\n"
    "<past_conversation>\n"
    "[Past conversations between the user and system will be placed here for context]\n"
    "</past_conversation>\n"
    "<current_conversation>\n"
    "[The current conversation between the user and system will be placed here]\n"
    "</current_conversation>\n"
    "Your job is to identify and categorize the user's preferences into two main types:\n"
    "- Explicit preferences: Directly stated preferences by the user.\n"
    "- Implicit preferences: Inferred from patterns, repeated inquiries, or contextual clues. Take a close look at user's request for implicit preferences.\n"
    "For explicit preference, extract only preference that the user has explicitly shared. Do not infer user's preference.\n"
    "For implicit preference, it is allowed to infer user's preference, but only the ones with strong signals, such as requesting something multiple times.\n"
    "Use Korean.\n"
)

SUMMARY_PROMPT = (
    "You will be given a text block and a list of summaries you previously generated when available.\n"
    "<task>\n"
    "- When the previously generated is not available, your goal is to summarize the given text block.\n"
    "- When there is existing summary, your goal is to extend summary by taking into account the given text block.\n"
    "- If there are queries/topics specified in the text block, your generated summary need to cover those queries/topics.\n"
    "- If there are instructions in the text block **guiding you how to generate summary**, you MUST follow them.\n"
    "</task>\n"
    "Use Korean.\n"
)

SEMANTIC_PROMPT = (
    "You are a long-term memory extraction agent supporting a lifelong learning system.\n"
    "Your task is to identify and extract meaningful information about the users from a given list of messages.\n"
    "Analyze the conversation and extract structured information about the user according to the schema below.\n"
    "Only include details that are explicitly stated or can be logically inferred from the conversation.\n"
    "- Extract information ONLY from the user messages. You should use assistant messages only as supporting context.\n"
    "- If the conversation contains no relevant or noteworthy information, return an empty list.\n"
    "- Do NOT extract anything from prior conversation history, even if provided. Use it solely for context.\n"
    "- Do NOT incorporate external knowledge.\n"
    "- Avoid duplicate extractions.\n"
    "Use Korean.\n"
)

SEMANTIC_CONSOLIDATION_PROMPT = (
    "You consolidate newly extracted facts with existing long-term semantic memories.\n"
    "- Merge duplicates; keep the most specific and recent facts.\n"
    "- Do not invent facts that were not extracted.\n"
    "- Prefer clear, atomic statements in Korean.\n"
    "Use Korean.\n"
)


def _strategy_namespaces(strategy: dict) -> list:
    return list(strategy.get("namespaces") or strategy.get("namespaceTemplates") or [])


def _existing_strategy_names(strategies: list) -> set:
    return {(s.get("name") or "") for s in (strategies or []) if s.get("name")}


def _build_user_preference_strategy() -> dict:
    return {
        "customMemoryStrategy": {
            "name": USER_PREFERENCE_STRATEGY_NAME,
            "namespaces": [USER_PREFERENCE_NAMESPACE_TEMPLATE],
            "configuration": {
                "userPreferenceOverride": {
                    "extraction": {
                        "modelId": MEMORY_EXTRACTION_MODEL_ID,
                        "appendToPrompt": USER_PREFERENCE_PROMPT,
                    }
                }
            },
        }
    }


def _build_summary_strategy() -> dict:
    return {
        "customMemoryStrategy": {
            "name": SUMMARY_STRATEGY_NAME,
            "namespaces": [SUMMARY_NAMESPACE_TEMPLATE],
            "configuration": {
                "summaryOverride": {
                    "consolidation": {
                        "modelId": MEMORY_EXTRACTION_MODEL_ID,
                        "appendToPrompt": SUMMARY_PROMPT,
                    }
                }
            },
        }
    }


def _build_semantic_strategy() -> dict:
    return {
        "customMemoryStrategy": {
            "name": SEMANTIC_STRATEGY_NAME,
            "namespaces": [SEMANTIC_NAMESPACE_TEMPLATE],
            "configuration": {
                "semanticOverride": {
                    "extraction": {
                        "modelId": MEMORY_EXTRACTION_MODEL_ID,
                        "appendToPrompt": SEMANTIC_PROMPT,
                    },
                    "consolidation": {
                        "modelId": MEMORY_EXTRACTION_MODEL_ID,
                        "appendToPrompt": SEMANTIC_CONSOLIDATION_PROMPT,
                    },
                }
            },
        }
    }


def shared_memory_strategies() -> list:
    """Strategy definitions shared by create_memory / installer / ensure."""
    return [
        _build_user_preference_strategy(),
        _build_summary_strategy(),
        _build_semantic_strategy(),
    ]
