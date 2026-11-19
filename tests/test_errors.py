from smart_db_assistant.eval.errors import categorize, parse_parts, schema_coverage

COLS = {
    "customers": {"customer_id", "name", "state"},
    "orders": {"order_id", "customer_id", "order_date"},
    "order_items": {"order_id", "product_id", "quantity"},
    "products": {"product_id", "name", "price"},
}
GOLD = ("SELECT c.name, SUM(oi.quantity) FROM customers c JOIN orders o ON o.customer_id = "
        "c.customer_id JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON "
        "p.product_id = oi.product_id WHERE p.name = 'Widget' GROUP BY c.name")


def test_bridge_detection():
    p = parse_parts(GOLD, COLS)
    assert p.tables == {"customers", "orders", "order_items", "products"}
    assert p.bridge_tables == {"orders"}          # only used to join


def test_implicit_join_and_unqualified_columns():
    p = parse_parts("SELECT name FROM customers, orders WHERE customers.customer_id = "
                    "orders.customer_id AND order_date > '2025-01-01'", COLS)
    assert len(p.joins) == 1
    assert ("orders", "order_date") in p.filter_cols


def test_schema_coverage():
    c = schema_coverage(["customers", "products", "reviews"], ["customers", "orders", "products"])
    assert c["schema_recall"] == round(2 / 3, 4)
    assert c["schema_precision"] == round(2 / 3, 4)
    assert c["schema_complete"] is False and c["missing_tables"] == ["orders"]


def _cat(pred, selected=None, **kw):
    return categorize(exec_match=False, action="sql", pred_sql=pred, gold_sql=GOLD,
                      selected=selected or list(COLS), columns=COLS, **kw)


def test_connector_vs_retrieval_miss():
    assert _cat(GOLD, selected=["customers", "order_items", "products"]) == "connector_miss"
    assert _cat(GOLD, selected=["customers", "orders", "order_items"]) == "retrieval_miss"


def test_generation_errors():
    assert categorize(exec_match=True, action="sql", pred_sql=GOLD, gold_sql=GOLD,
                      selected=list(COLS)) == "correct"
    assert _cat(GOLD.replace("'Widget'", "'Gadget'")) == "wrong_filter"
    assert _cat(GOLD.replace("SUM(", "COUNT(")) == "wrong_aggregation"
    assert _cat(GOLD.replace("SELECT c.name,", "SELECT c.state,")) == "wrong_columns"
    assert _cat(GOLD.replace("oi.order_id = o.order_id", "oi.product_id = o.order_id")) \
        == "wrong_join"
    assert _cat("SELECT name FROM customers") == "wrong_tables"
    assert _cat(GOLD, distractor_tables=["other_db__x"]) == "distractor_table"
    assert categorize(exec_match=False, action="error", pred_sql=None, gold_sql=GOLD,
                      selected=list(COLS), db_error="no such column") == "exec_error"
