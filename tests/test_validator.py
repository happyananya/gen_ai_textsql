import pytest

from smart_db_assistant.validator import validate_sql

KNOWN = {
    "customers": {"customer_id", "first_name", "state", "signup_date"},
    "orders": {"order_id", "customer_id", "order_date", "status"},
}


def test_simple_select_gets_limit():
    r = validate_sql("SELECT COUNT(*) FROM customers WHERE state = 'California'", KNOWN, 500)
    assert r.ok, r.errors
    assert "LIMIT 500" in r.sql


def test_existing_limit_kept():
    r = validate_sql("SELECT first_name FROM customers LIMIT 5", KNOWN, 500)
    assert r.ok and "LIMIT 5" in r.sql and "500" not in r.sql


def test_cte_and_join_ok():
    sql = """WITH c AS (SELECT customer_id FROM orders WHERE status = 'cancelled')
             SELECT cu.first_name FROM customers cu JOIN c ON c.customer_id = cu.customer_id"""
    r = validate_sql(sql, KNOWN)
    assert r.ok, r.errors
    assert r.tables == {"customers", "orders"}


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders WHERE status = 'cancelled'",
    "UPDATE customers SET state = 'X'",
    "DROP TABLE customers",
    "INSERT INTO customers (first_name) VALUES ('x')",
    "SELECT 1; DROP TABLE customers",
    "SELECT pg_sleep(100)",
    "SELECT * INTO backup FROM customers",
    "TRUNCATE orders",
])
def test_rejects_writes_and_tricks(sql):
    assert not validate_sql(sql, KNOWN).ok


def test_unknown_table_and_column():
    assert "unknown table" in validate_sql("SELECT * FROM profits", KNOWN).errors[0]
    r = validate_sql("SELECT c.cost FROM customers c", KNOWN)
    assert not r.ok and any("unknown column" in e for e in r.errors)


def test_alias_not_flagged():
    r = validate_sql("SELECT state, COUNT(*) AS n FROM customers GROUP BY state ORDER BY n DESC", KNOWN)
    assert r.ok, r.errors
