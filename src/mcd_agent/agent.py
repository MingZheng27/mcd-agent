from __future__ import annotations

import logging
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .config import Settings
from .context import ContextManager, SessionStore
from .mcd_mcp_client import McdMcpClient
from .llm import build_llm
from .nutrition import NutritionAnalyzer, NutritionCatalog, RecommendationEngine
from .prompts import SYSTEM_PROMPT
from .tools import build_tools

logger = logging.getLogger(__name__)


class AgentGraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class McdOrderingAgent:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session_store = SessionStore(settings.session_store_path)
        self.context_manager = ContextManager()
        self.client = McdMcpClient(settings)
        self.catalog = NutritionCatalog(settings.nutrition_catalog_path)
        self.recommender = RecommendationEngine(self.catalog)
        self.nutrition_analyzer = NutritionAnalyzer(self.catalog, self.client)
        self.llm = build_llm(settings)

    def _build_graph(self, session_id: str):
        state = self.session_store.load(session_id)
        tools = build_tools(
            settings=self.settings,
            session_state=state,
            client=self.client,
            recommender=self.recommender,
            nutrition_analyzer=self.nutrition_analyzer,
        )
        llm_with_tools = self.llm.bind_tools(tools)
        tool_node = ToolNode(tools)

        def assistant_node(graph_state: AgentGraphState) -> dict[str, list[BaseMessage]]:
            response = llm_with_tools.invoke(graph_state["messages"])
            return {"messages": [response]}

        def route_after_assistant(graph_state: AgentGraphState) -> str:
            last_message = graph_state["messages"][-1]
            if isinstance(last_message, AIMessage) and last_message.tool_calls:
                return "tools"
            return END

        builder = StateGraph(AgentGraphState)
        builder.add_node("assistant", assistant_node)
        builder.add_node("tools", tool_node)
        builder.add_edge(START, "assistant")
        builder.add_conditional_edges("assistant", route_after_assistant, {"tools": "tools", END: END})
        builder.add_edge("tools", "assistant")
        return builder.compile(), state

    def invoke(self, session_id: str, user_input: str) -> str:
        graph, state = self._build_graph(session_id)
        self.context_manager.append_user_message(state, user_input)

        chat_history: list[BaseMessage] = []
        for message in state.history[:-1]:
            if message["role"] == "user":
                chat_history.append(HumanMessage(content=message["content"]))
            else:
                chat_history.append(AIMessage(content=message["content"]))

        runtime_messages: list[BaseMessage] = [
            SystemMessage(content=SYSTEM_PROMPT),
            AIMessage(
                content=(
                    f"当前会话摘要:\n{state.rolling_summary or '暂无'}\n\n"
                    f"当前用户偏好:\n{state.preference.model_dump_json(indent=2)}\n\n"
                    f"当前订单草稿:\n{state.order_draft.model_dump_json(indent=2)}"
                )
            ),
            *chat_history,
            HumanMessage(content=user_input),
        ]

        logger.info("Invoking LangGraph agent for session=%s input=%s", session_id, user_input)
        should_append_agent_message = True
        try:
            result = graph.invoke({"messages": runtime_messages})
            output = self._extract_final_answer(result["messages"])
        except Exception as exc:  # noqa: BLE001
            logger.exception("Agent invocation failed for session=%s", session_id)
            output = self._fallback_response(exc)
            should_append_agent_message = False
        if should_append_agent_message:
            self.context_manager.append_agent_message(state, output)
        self.session_store.save(state)
        return output

    @staticmethod
    def _fallback_response(exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        lower_message = message.lower()

        if "insufficient_quota" in lower_message or "quota" in lower_message or "rate limit" in lower_message:
            return "当前模型服务可达，但账号额度不足，暂时无法生成智能回复。请补充 OpenAI 配额、切换到可用模型，或改用其他已配置的 LLM 提供方。"
        if "invalid api key" in lower_message or "authorized_error" in lower_message or "401" in lower_message:
            return "当前模型服务鉴权失败，agent 已启动但本轮未能完成推理。请检查 .env 中配置的 API Key 是否正确、是否已过期，以及所选 provider 是否匹配。"
        if "connection error" in lower_message or "connecterror" in lower_message:
            return "当前无法连接到模型服务，agent 已启动但本轮未能完成推理。请检查网络、代理或模型服务地址后重试。"
        return f"agent 已启动，但本轮处理失败：{message}"

    @staticmethod
    def _extract_final_answer(messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, AIMessage) and message.content:
                if isinstance(message.content, str):
                    return message.content
                if isinstance(message.content, list):
                    text_parts = [part.get("text", "") for part in message.content if isinstance(part, dict)]
                    if text_parts:
                        return "\n".join(part for part in text_parts if part)
        raise ValueError("LangGraph 未返回可用的最终回答。")
