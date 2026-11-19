from smart_db_assistant.schema import Column, ForeignKey, SchemaMetadata, Table
from smart_db_assistant.schema_graph import expand, fk_graph, join_path_completion


def ecommerce() -> SchemaMetadata:
    def t(name, fks=()):
        return Table(name, [Column("id", "int")],
                     foreign_keys=[ForeignKey(c, r, "id") for c, r in fks])
    return SchemaMetadata({
        "customers": t("customers"),
        "categories": t("categories"),
        "suppliers": t("suppliers"),
        "products": t("products", [("category_id", "categories"), ("supplier_id", "suppliers")]),
        "orders": t("orders", [("customer_id", "customers")]),
        "order_items": t("order_items", [("order_id", "orders"), ("product_id", "products")]),
        "reviews": t("reviews", [("product_id", "products"), ("customer_id", "customers")]),
        "audit_log": t("audit_log"),                       # isolated
    })


def test_graph_is_undirected():
    g = fk_graph(ecommerce())
    assert "orders" in g["customers"] and "customers" in g["orders"]


def test_bridge_table_added():
    # customers -> orders -> order_items -> products: 2 connectors needed, and the
    # shortest path wins over the shorter-looking reviews route only if it is shorter.
    e = join_path_completion(ecommerce(), ["customers", "categories"])
    assert e.tables[:2] == ["customers", "categories"]
    assert "products" in e.connectors
    assert set(e.connectors) <= {"products", "reviews", "orders", "order_items"}
    assert e.unconnected == []


def test_five_table_join_from_three_retrieved():
    # The feedback's example: k=3 retrieved, the 2 connector tables must be recovered.
    e = join_path_completion(ecommerce(), ["customers", "order_items", "categories"])
    assert {"orders", "products"} <= set(e.tables)
    assert len(e.tables) == 5


def test_no_expansion_when_already_connected():
    e = join_path_completion(ecommerce(), ["orders", "customers"])
    assert e.connectors == []


def test_unreachable_kept_and_reported():
    e = join_path_completion(ecommerce(), ["orders", "audit_log"])
    assert "audit_log" in e.tables and e.unconnected == ["audit_log"]


def test_fk1_adds_all_neighbours():
    e = expand(ecommerce(), ["products"], "fk1")
    assert set(e.connectors) == {"categories", "suppliers", "order_items", "reviews"}


def test_none_strategy():
    assert expand(ecommerce(), ["orders"], "none").tables == ["orders"]
