"""Databricks AI/BI Genie Conversation API integration.

Wraps the Genie Conversation REST API so the Streamlit app can embed a
conversational data interface.  Uses the app's WorkspaceClient (SP credentials)
— all Genie queries therefore run at the SP's Unity Catalog privilege level.

For per-user RLS/CLS enforcement (OBO), forward the user's OAuth token as the
Authorization header instead of using the shared WorkspaceClient.

API reference:
  POST   /api/2.0/genie/spaces/{space_id}/start-conversation
  POST   /api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages
  GET    /api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages/{msg_id}
  GET    /api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages/{msg_id}/query-result
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Result container


@dataclass
class GenieResult:
    """Parsed response from one Genie message."""
    text_response: str = ""
    sql: Optional[str] = None
    col_names: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def has_data(self) -> bool:
        return bool(self.rows)


# ---------------------------------------------------------------------------
# Public API


def start_conversation(space_id: str, question: str) -> tuple[str, str]:
    """Start a new Genie conversation.  Returns (conversation_id, message_id)."""
    resp = _api("POST", f"/api/2.0/genie/spaces/{space_id}/start-conversation",
                body={"content": question})
    conv_id = resp.get("conversation_id") or resp.get("id", "")
    msg = resp.get("message", {})
    msg_id = msg.get("id", "")
    return conv_id, msg_id


def send_message(space_id: str, conv_id: str, question: str) -> str:
    """Send a follow-up message to an existing conversation.  Returns message_id."""
    resp = _api(
        "POST",
        f"/api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages",
        body={"content": question},
    )
    return resp.get("id", "")


def poll_result(
    space_id: str,
    conv_id: str,
    msg_id: str,
    timeout_seconds: int = 120,
) -> GenieResult:
    """Poll until the Genie message finishes executing, then return the result.

    Terminal statuses: COMPLETED, FAILED, CANCELLED, QUERY_RESULT_EXPIRED.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        msg = _api(
            "GET",
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages/{msg_id}",
        )
        status = (msg.get("status") or "").upper()

        if status in ("COMPLETED",):
            return _extract_result(space_id, conv_id, msg_id, msg)

        if status in ("FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"):
            err = msg.get("error", {}).get("message") or f"Genie status: {status}"
            return GenieResult(error=err)

        # Still running — brief sleep before next poll
        time.sleep(2)

    return GenieResult(error=f"Genie did not respond within {timeout_seconds}s.")


def ask(space_id: str, question: str, conv_id: Optional[str] = None) -> tuple[GenieResult, str, str]:
    """High-level helper: send a question, poll, return (result, conv_id, msg_id).

    If conv_id is provided, sends a follow-up; otherwise starts a new conversation.
    """
    if conv_id:
        msg_id = send_message(space_id, conv_id, question)
    else:
        conv_id, msg_id = start_conversation(space_id, question)

    result = poll_result(space_id, conv_id, msg_id)
    return result, conv_id, msg_id


# ---------------------------------------------------------------------------
# Internal helpers


def _api(method: str, path: str, body: Optional[dict] = None) -> dict:
    from .catalog import _workspace_client
    w = _workspace_client()
    kwargs: dict = {}
    if body is not None:
        kwargs["body"] = body
    try:
        return w.api_client.do(method, path, **kwargs) or {}
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "PERMISSION_DENIED" in msg or "not authorized" in msg.lower():
            space_id = path.split("/spaces/")[1].split("/")[0] if "/spaces/" in path else "?"
            raise PermissionError(
                f"The app service principal does not have access to Genie Space '{space_id}'.\n\n"
                f"Fix: Databricks workspace → AI/BI → Genie Spaces → your space → "
                f"Permissions → Add SP '39ee93a7-c623-4614-90a8-c3798bb5b329' with Can Run."
            ) from exc
        raise


def _extract_result(space_id: str, conv_id: str, msg_id: str, msg: dict) -> GenieResult:
    """Parse the completed message — fetch text response, SQL, and query result."""
    # Text response lives in the message's attachments or top-level fields.
    text = (
        msg.get("text_response")
        or msg.get("content")
        or _first_text_attachment(msg)
        or ""
    )

    # Query result (SQL + rows) may be in the message or need a separate GET.
    sql: Optional[str] = None
    col_names: list[str] = []
    rows: list[list] = []

    try:
        qr = _api(
            "GET",
            f"/api/2.0/genie/spaces/{space_id}/conversations/{conv_id}"
            f"/messages/{msg_id}/query-result",
        )
        statement = qr.get("statement_response", {})
        sql = statement.get("statement", "") or None

        schema = statement.get("result", {}).get("data_array", None)
        manifest = statement.get("manifest", {})

        if manifest.get("schema"):
            col_names = [c.get("name", "") for c in manifest["schema"].get("columns", [])]

        raw = statement.get("result", {}).get("data_array") or []
        rows = [list(r) for r in raw]

    except Exception:
        pass  # query result is optional — text response still valuable

    return GenieResult(
        text_response=text.strip(),
        sql=sql,
        col_names=col_names,
        rows=rows,
    )


def _first_text_attachment(msg: dict) -> str:
    """Extract the first text-type attachment from a Genie message."""
    for att in msg.get("attachments", []):
        txt = att.get("text", {})
        if txt.get("content"):
            return txt["content"]
        qry = att.get("query", {})
        if qry.get("description"):
            return qry["description"]
    return ""
