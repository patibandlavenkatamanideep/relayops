"""FastAPI service boundary for RelayOps (v1.8).

A thin HTTP layer that *wraps* the existing synchronous pipeline (``handle_turn``)
rather than reimplementing it. Every ``POST /v1/turn`` mints a request-scoped
turn id, runs the same gate -> route -> tool/RAG -> guardrail -> respond pipeline,
and writes the same durable audit record the Streamlit UI uses — so the API,
the audit row, and the logs all share one id.

The LLM arm stays opt-in and disabled by default: the service uses
``get_composer()``, which returns the deterministic template composer unless
``RELAYOPS_COMPOSER=llm`` AND ``RELAYOPS_ALLOW_LLM=true`` AND a key are set.
"""
