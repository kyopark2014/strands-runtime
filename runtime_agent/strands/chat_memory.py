"""Local chat memory and AgentCore Memory helpers."""

import logging

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger("chat")


class SimpleMemory:
    def __init__(self, max_messages=5):
        self.max_messages = max_messages
        self.chat_memory = SimpleChatMemory()

    def load_memory_variables(self, inputs):
        messages = self.chat_memory.messages
        if len(messages) > self.max_messages:
            return {"chat_history": messages[-self.max_messages :]}
        return {"chat_history": messages}


class SimpleChatMemory:
    def __init__(self):
        self.messages = []

    def add_user_message(self, message):
        self.messages.append(HumanMessage(content=message))

    def add_ai_message(self, message):
        self.messages.append(AIMessage(content=message))

    def clear(self):
        self.messages = []


def initiate():
    import chat

    # Preserve the logged-in user_id for AgentCore Memory actor isolation.
    # Do NOT replace it with a random UUID.
    effective_user_id = (
        chat.user_id if chat.user_id and str(chat.user_id).strip() else "default"
    )
    if effective_user_id != chat.user_id:
        chat.user_id = effective_user_id
        logger.info(f"user_id fallback for local memory: {chat.user_id}")

    # general conversation memory (local short-term, not AgentCore Memory)
    if chat.user_id in chat.map_chain:
        logger.info(f"memory exist. reuse it!")
        chat.memory_chain = chat.map_chain[chat.user_id]
    else:
        logger.info(f"memory not exist. create new memory!")
        chat.memory_chain = SimpleMemory(max_messages=5)
        chat.map_chain[chat.user_id] = chat.memory_chain


def clear_chat_history():
    import chat

    # Initialize memory_chain if it doesn't exist
    if chat.memory_chain is None:
        initiate()

    if chat.memory_chain and hasattr(chat.memory_chain, "chat_memory"):
        chat.memory_chain.chat_memory.clear()
    else:
        chat.memory_chain = SimpleMemory(max_messages=5)
    chat.map_chain[chat.user_id] = chat.memory_chain


def save_chat_history(text, msg):
    import chat

    # Initialize memory_chain if it doesn't exist
    if chat.memory_chain is None:
        initiate()

    if chat.memory_chain and hasattr(chat.memory_chain, "chat_memory"):
        chat.memory_chain.chat_memory.add_user_message(text)
        if len(msg) > chat.MSG_LENGTH:
            chat.memory_chain.chat_memory.add_ai_message(msg[: chat.MSG_LENGTH])
        else:
            chat.memory_chain.chat_memory.add_ai_message(msg)


#########################################################
# AgentCore Memory
#########################################################
def initiate_memory():
    """Load or create AgentCore Memory session variables for the current user."""
    import agentcore_memory
    import chat

    effective_user_id = (
        chat.user_id if chat.user_id and str(chat.user_id).strip() else "default"
    )
    logger.info(f"initiate_memory for user_id: {effective_user_id}")

    chat.memory_id, chat.actor_id, chat.session_id, namespace = (
        agentcore_memory.load_memory_variables(effective_user_id)
    )
    if not namespace:
        namespace = f"/users/{chat.actor_id}/preferences"
    logger.info(
        f"memory_id: {chat.memory_id}, actor_id: {chat.actor_id}, "
        f"session_id: {chat.session_id}, namespace: {namespace}"
    )

    if chat.memory_id is None:
        chat.memory_id = agentcore_memory.retrieve_memory_id()
        if chat.memory_id is None:
            logger.info("Memory will be created...")
            chat.memory_id = agentcore_memory.create_memory()
            logger.info(f"Memory was created... {chat.memory_id}")

    agentcore_memory.create_strategy_if_not_exists(chat.memory_id)


def save_to_memory(query, result):
    """Save conversation to AgentCore Memory when memory_enabled is True."""
    import agentcore_memory
    import chat

    if not chat.memory_enabled:
        return

    try:
        expected_actor = agentcore_memory.resolve_memory_actor_id(
            chat.user_id if chat.user_id and str(chat.user_id).strip() else "default"
        )
        if chat.memory_id is None or chat.actor_id != expected_actor:
            initiate_memory()

        agentcore_memory.save_conversation_to_memory(
            chat.memory_id, chat.actor_id, chat.session_id, query, result
        )
        logger.info(
            f"Saved conversation to AgentCore Memory for actor_id={chat.actor_id}"
        )
    except Exception as e:
        logger.error(f"Failed to save conversation to AgentCore Memory: {e}")
