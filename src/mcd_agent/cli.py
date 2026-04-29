from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from .agent import McdOrderingAgent
from .config import get_settings
from .logging_config import configure_logging


def _format_agent_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    lower_message = message.lower()

    if "insufficient_quota" in lower_message or "quota" in lower_message or "rate limit" in lower_message:
        return "LLM 调用失败：当前 OpenAI API 配额不足或已触发限流，请检查账号额度、账单，或切换到可用模型/提供方。"
    if "connection error" in lower_message or "connecterror" in lower_message:
        return "LLM 调用失败：当前无法连接到模型服务，请检查网络连通性或代理配置。"
    return f"Agent error: {message}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Nutrition-aware McDonald's ordering agent")
    parser.add_argument("--session-id", default="default", help="Persistent session identifier")
    parser.add_argument("--message", help="Single-turn input message")
    args = parser.parse_args()

    load_dotenv()
    settings = get_settings()
    configure_logging(settings.log_dir)
    agent = McdOrderingAgent(settings)

    if args.message:
        try:
            print(agent.invoke(args.session_id, args.message))
        except Exception as exc:  # noqa: BLE001
            print(_format_agent_error(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        return

    print("McDonald's Agent 已启动，输入 quit 退出。")
    while True:
        try:
            user_input = input("You> ").strip()
        except EOFError:
            print()
            return

        if user_input.lower() in {"quit", "exit"}:
            return
        if not user_input:
            continue

        try:
            reply = agent.invoke(args.session_id, user_input)
        except Exception as exc:  # noqa: BLE001
            print(_format_agent_error(exc), file=sys.stderr)
            continue
        print(f"Agent> {reply}")


if __name__ == "__main__":
    main()
