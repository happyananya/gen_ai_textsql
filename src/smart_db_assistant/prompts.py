"""Prompt templates and the structured-output schema."""

from __future__ import annotations

SQL_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["sql", "refuse", "clarify"],
            "description": "sql = answerable with a SELECT; refuse = unsafe or unanswerable "
                           "from this schema; clarify = ambiguous, ask the user one question.",
        },
        "sql": {"type": "string", "description": "A single PostgreSQL SELECT query, or empty."},
        "message": {
            "type": "string",
            "description": "For refuse/clarify: a short explanation or question for the user. "
                           "For sql: one sentence on what the query computes.",
        },
    },
    "required": ["action", "sql", "message"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You translate business questions into PostgreSQL for a read-only analytics assistant.
The people asking are managers and analysts who do not know SQL or the schema.

Database schema:
{schema}

Rules:
- Return a single SELECT (CTEs allowed). Never write INSERT/UPDATE/DELETE/DDL or anything that \
changes data; if asked to, set action="refuse" and explain that the assistant is read-only.
- Use only the tables and columns shown above. If the question needs data that does not exist \
(for example a cost column), set action="refuse" and say what is missing.
- If a business term is genuinely ambiguous and the choice would change the answer, set \
action="clarify" and ask one short question. Otherwise pick the common interpretation and \
state it in "message".
- Revenue = SUM(order_items.quantity * order_items.unit_price * (1 - order_items.discount)) \
excluding cancelled orders, unless the user says otherwise.
- Relative dates ("last month", "this year") are relative to CURRENT_DATE.
- Match text case-insensitively (ILIKE) for names, states and cities.
- Return readable columns (names, not only ids) and add ORDER BY when the user asks for top/bottom.
"""

REPAIR_PROMPT = """The previous SQL failed.

SQL:
{sql}

Error:
{error}

Fix the query. Keep the same intent and follow all the rules."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "One or two plain-English sentences."},
        "chart": {
            "type": "string",
            "enum": ["none", "bar", "line"],
            "description": "line for time series, bar for category comparisons, else none.",
        },
        "x": {"type": "string", "description": "Column for the x axis, or empty."},
        "y": {"type": "string", "description": "Numeric column for the y axis, or empty."},
    },
    "required": ["answer", "chart", "x", "y"],
    "additionalProperties": False,
}

SUMMARY_PROMPT = """Question: {question}

Result columns: {columns}
First rows ({shown} of {total}{truncated}):
{rows}

Answer the question in one or two sentences for a non-technical reader, using only the data \
shown. If the result is empty, say that no matching records were found. Suggest a chart only \
when it helps."""
