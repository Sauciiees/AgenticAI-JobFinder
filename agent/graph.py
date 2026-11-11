import os
import sqlite3

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

import config
from agent.nodes import (
  application_tracker_node,
  asset_tailoring_agent,
  auto_apply_node,
  conversational_router,
  general_chat_node,
  human_approval_node,
  job_search_agent,
  job_selection_node,
  matching_screening_agent,
)
from agent.state import State
from tools import ALL_TOOLS


def route_by_intent(state: State):
  """Conditional edge: routes to chat or job pipeline based on classified intent."""
  intent = state.get("route_intent", "chat")
  if intent == "job_search":
    return "job_discovery_agent"
  else:
    return "general_chat_node"


workflow = StateGraph(State)

# 1. Add all nodes
workflow.add_node("conversational_router", conversational_router)
workflow.add_node("general_chat_node", general_chat_node)
workflow.add_node("job_discovery_agent", job_search_agent)
workflow.add_node("job_selection_node", job_selection_node)
workflow.add_node("tools", ToolNode(ALL_TOOLS))
workflow.add_node("matching_screening_agent", matching_screening_agent)
workflow.add_node("asset_tailoring_agent", asset_tailoring_agent)
workflow.add_node("human_approval_node", human_approval_node)
workflow.add_node("auto_apply_node", auto_apply_node)
workflow.add_node("application_tracker_node", application_tracker_node)

# 2. Entry point: always go to the router first
workflow.add_edge(START, "conversational_router")

# 3. Router decides: chat or job search pipeline
workflow.add_conditional_edges(
    "conversational_router",
    route_by_intent,
    {
        "general_chat_node": "general_chat_node",
        "job_discovery_agent": "job_discovery_agent",
    },
)

# 4. Chat node ends the conversation
workflow.add_edge("general_chat_node", END)

# 5. Job search pipeline:
#    Discovery -> Selection (interrupt) -> Matching -> Tailoring -> Human Review -> Auto Apply -> Tracker
workflow.add_edge("job_discovery_agent", "job_selection_node")
workflow.add_edge("job_selection_node", "matching_screening_agent")
workflow.add_edge("matching_screening_agent", "asset_tailoring_agent")
workflow.add_edge("asset_tailoring_agent", "human_approval_node")
workflow.add_edge("human_approval_node", "auto_apply_node")
workflow.add_edge("auto_apply_node", "application_tracker_node")
workflow.add_edge("application_tracker_node", END)

# 6. Compile graph with checkpointer
def _build_checkpointer():
    """Postgres when DATABASE_URL is Postgres (the hosted demo), otherwise a local SQLite file."""
    if config.IS_POSTGRES:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        conninfo = config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
        pool = ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=5,
            # prepare_threshold=0: works through PgBouncer-style poolers (Neon's pooled endpoint).
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            check=ConnectionPool.check_connection,
            open=True,
        )
        saver = PostgresSaver(pool)
        saver.setup()
        return saver

    from langgraph.checkpoint.sqlite import SqliteSaver

    os.makedirs(os.path.dirname(config.CHECKPOINT_DB) or ".", exist_ok=True)
    conn = sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False)
    return SqliteSaver(conn)


memory = _build_checkpointer()
app = workflow.compile(checkpointer=memory)


def delete_threads(thread_ids: list[str]) -> None:
    """Removes the agent's saved state for these chats (account deletion, retention)."""
    for thread_id in thread_ids:
        memory.delete_thread(thread_id)
