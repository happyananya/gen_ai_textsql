"""Chat UI.  Run:  streamlit run app/streamlit_app.py

Owner: Harindham.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from smart_db_assistant.config import get_settings
from smart_db_assistant.pipeline import Assistant, Turn

st.set_page_config(page_title="Smart Database Assistant", page_icon="🗄️", layout="wide")


@st.cache_resource
def get_assistant(pipeline: str, model: str) -> Assistant:
    return Assistant(get_settings(pipeline=pipeline, model=model))


with st.sidebar:
    st.header("Settings")
    pipeline = st.radio("Pipeline", ["baseline", "rag"], index=0)
    model = st.text_input("Model", get_settings().model)
    show_sql = st.toggle("Show SQL", value=True)
    use_followups = st.toggle("Use previous questions as context", value=True)
    if st.button("Clear chat"):
        st.session_state.messages = []

st.title("Smart Database Assistant")
st.caption("Ask questions about customers, orders, products and more, in plain English.")

if "messages" not in st.session_state:
    st.session_state.messages = []


def render(msg: dict) -> None:
    st.markdown(msg["text"])
    if msg.get("sql") and show_sql:
        with st.expander("SQL"):
            st.code(msg["sql"], language="sql")
    if msg.get("df") is not None:
        df = msg["df"]
        chart = msg.get("chart") or {}
        if chart.get("x") in df.columns and chart.get("y") in df.columns:
            data = df.set_index(chart["x"])[chart["y"]]
            (st.line_chart if chart["type"] == "line" else st.bar_chart)(data)
        st.dataframe(df, use_container_width=True)


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        render(msg)

if question := st.chat_input("e.g. Top 5 products by revenue last month"):
    st.session_state.messages.append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)

    history = []
    if use_followups:
        msgs = st.session_state.messages[:-1]
        for u, a in zip(msgs[::2], msgs[1::2]):
            if a.get("sql"):
                history.append(Turn(u["text"], a["sql"]))
        history = history[-3:]

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                ans = get_assistant(pipeline, model).ask(question, history=history)
            except Exception as e:  # noqa: BLE001
                st.error(f"Something went wrong: {e}")
                st.stop()
        msg = {"role": "assistant", "sql": ans.sql, "chart": ans.chart}
        if ans.action == "sql" and ans.result is not None:
            df = pd.DataFrame(ans.result.rows, columns=ans.result.columns)
            msg["df"] = df
            msg["text"] = ans.summary or ans.message
            if ans.result.truncated:
                msg["text"] += f"\n\n_Showing the first {len(df)} rows._"
        else:
            msg["text"] = ans.message
        render(msg)
        st.session_state.messages.append(msg)
