"""End-to-end benchmark harness on two tiny Spider-format databases, with a fake LLM."""

import json
import sqlite3
import sys

import pytest

from smart_db_assistant.benchmark import run as bench_run
from smart_db_assistant.benchmark.datasets import load_questions, load_schemas
from smart_db_assistant.benchmark.pool import PooledBackend, build_pool, to_prefixed
from smart_db_assistant.db import SQLiteBackend
from smart_db_assistant.llm import LLMResponse

SHOP = {
    "db_id": "shop",
    "table_names_original": ["customer", "purchase", "item"],
    "table_names": ["customer", "purchase", "item"],
    "column_names_original": [[-1, "*"], [0, "id"], [0, "name"], [1, "id"], [1, "customer_id"],
                              [1, "item_id"], [2, "id"], [2, "title"]],
    "column_names": [[-1, "*"], [0, "id"], [0, "name"], [1, "id"], [1, "customer id"],
                     [1, "item id"], [2, "id"], [2, "title"]],
    "column_types": ["text", "number", "text", "number", "number", "number", "number", "text"],
    "primary_keys": [1, 3, 6],
    "foreign_keys": [[4, 1], [5, 6]],
}
ZOO = {
    "db_id": "zoo",
    "table_names_original": ["animal", "customer"],      # name collision with shop.customer
    "table_names": ["animal", "customer"],
    "column_names_original": [[-1, "*"], [0, "id"], [0, "species"], [1, "id"], [1, "name"]],
    "column_names": [[-1, "*"], [0, "id"], [0, "species"], [1, "id"], [1, "name"]],
    "column_types": ["text", "number", "text", "number", "text"],
    "primary_keys": [1, 3],
    "foreign_keys": [],
}
GOLD = ("SELECT T1.name FROM customer AS T1 JOIN purchase AS T2 ON T1.id = T2.customer_id "
        "JOIN item AS T3 ON T2.item_id = T3.id WHERE T3.title = 'Lamp'")


@pytest.fixture
def data(tmp_path):
    (tmp_path / "tables.json").write_text(json.dumps([SHOP, ZOO]))
    (tmp_path / "dev.json").write_text(json.dumps([
        {"db_id": "shop", "question": "Who bought a Lamp?", "query": GOLD}]))
    for db, ddl in {
        "shop": ["CREATE TABLE customer (id INTEGER PRIMARY KEY, name TEXT)",
                 "CREATE TABLE item (id INTEGER PRIMARY KEY, title TEXT)",
                 "CREATE TABLE purchase (id INTEGER PRIMARY KEY, customer_id INT, item_id INT)",
                 "INSERT INTO customer VALUES (1,'Ann'),(2,'Bo')",
                 "INSERT INTO item VALUES (1,'Lamp'),(2,'Desk')",
                 "INSERT INTO purchase VALUES (1,1,1),(2,2,2)"],
        "zoo": ["CREATE TABLE animal (id INTEGER PRIMARY KEY, species TEXT)",
                "CREATE TABLE customer (id INTEGER PRIMARY KEY, name TEXT)"],
    }.items():
        (tmp_path / "db" / db).mkdir(parents=True)
        con = sqlite3.connect(tmp_path / "db" / db / f"{db}.sqlite")
        for stmt in ddl:
            con.execute(stmt)
        con.commit()
        con.close()
    return tmp_path


def test_load_and_pool(data):
    schemas = load_schemas(data / "tables.json", [data / "db"])
    assert schemas["shop"].tables["purchase"].foreign_keys[0].ref_table == "customer"
    assert load_questions(data / "dev.json")[0].gold_sql == GOLD

    pool, dbs = build_pool("shop", schemas, n_tables=0)
    assert set(pool.tables) == {"shop__customer", "shop__purchase", "shop__item"} and dbs == ["shop"]
    pool, dbs = build_pool("shop", schemas, n_tables=4)
    assert "zoo__customer" in pool.tables and len(pool) == 5
    assert pool.tables["shop__purchase"].foreign_keys[0].ref_table == "shop__customer"
    assert to_prefixed("shop", ["customer", "item"], pool) == ["shop__customer", "shop__item"]


def test_pooled_backend_rewrites_and_blocks_distractors(data):
    be = PooledBackend("shop", SQLiteBackend(data / "db" / "shop" / "shop.sqlite"))
    assert be.execute("SELECT name FROM shop__customer ORDER BY id").rows == [("Ann",), ("Bo",)]
    with pytest.raises(sqlite3.OperationalError, match="no such table: zoo__customer"):
        be.execute("SELECT name FROM zoo__customer")
    assert be.distractors_used == ["zoo__customer"]


class FakeLLM:
    model = "fake"

    def __init__(self, sql):
        self.sql = sql

    def generate_json(self, system, messages, schema):
        return LLMResponse({"action": "sql", "sql": self.sql, "message": ""}, "fake", 100, 10, 1.0)


def _run(data, monkeypatch, *extra, sql=None):
    out = data / "out"
    monkeypatch.setattr(bench_run, "make_client", lambda *a, **k: FakeLLM(sql))
    monkeypatch.setattr(sys, "argv", [
        "sda-bench", "--questions", str(data / "dev.json"), "--tables", str(data / "tables.json"),
        "--db-root", str(data / "db"), "--out", str(out), "--tag", "t", *extra])
    bench_run.main()
    item = json.loads((out / "t" / "items.jsonl").read_text().splitlines()[0])
    return item, json.loads((out / "t" / "summary.json").read_text())


PRED = ("SELECT c.name FROM shop__customer c JOIN shop__purchase p ON c.id = p.customer_id "
        "JOIN shop__item i ON p.item_id = i.id WHERE i.title = 'Lamp'")


def test_end_to_end_correct(data, monkeypatch):
    item, summary = _run(data, monkeypatch, "--pool-tables", "5", "--method", "full", sql=PRED)
    assert item["exec_match"] is True and item["category"] == "correct"
    assert item["n_gold_tables"] == 3 and item["bridge_tables"] == ["purchase"]
    assert item["schema_complete"] is True and item["pool_tables"] == 5
    assert summary["execution_accuracy"] == 1.0


def test_end_to_end_connector_miss_and_joinpath(data, monkeypatch):
    # k=2 retrieves customer + item; without expansion the bridge (purchase) is missing.
    item, _ = _run(data, monkeypatch, "--method", "rag", "--k", "2", "--expansion", "none",
                   "--retrieval-only")
    assert item["schema_complete"] is False and item["missing_tables"] == ["purchase"]
    item, _ = _run(data, monkeypatch, "--method", "rag", "--k", "2", "--expansion", "joinpath",
                   "--retrieval-only")
    assert item["schema_complete"] is True and item["connector_tables"] == ["shop__purchase"]


def test_end_to_end_distractor(data, monkeypatch):
    item, _ = _run(data, monkeypatch, "--pool-tables", "5", "--method", "full",
                   "--max-retries", "0", sql="SELECT name FROM zoo__customer")
    assert item["exec_match"] is False and item["category"] == "distractor_table"
