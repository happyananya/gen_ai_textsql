"""Database metadata: introspect tables/columns/FKs and render them for the LLM.

Owner: Gurleen (retrieval uses these objects) / Harindham (baseline prompt).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    data_type: str
    nullable: bool = True
    comment: str | None = None
    samples: list[str] = field(default_factory=list)   # a few distinct example values


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    comment: str | None = None
    db_id: str | None = None          # source database (benchmarks / pooled schemas)

    def to_ddl(self) -> str:
        """Compact CREATE TABLE text - the format LLMs read most reliably."""
        lines = []
        for c in self.columns:
            line = f"  {c.name} {c.data_type}"
            if not c.nullable:
                line += " NOT NULL"
            if c.comment:
                line += f"  -- {c.comment}"
            lines.append(line)
        if self.primary_key:
            lines.append(f"  PRIMARY KEY ({', '.join(self.primary_key)})")
        for fk in self.foreign_keys:
            lines.append(f"  FOREIGN KEY ({fk.column}) REFERENCES {fk.ref_table}({fk.ref_column})")
        header = f"-- {self.comment}\n" if self.comment else ""
        return header + f"CREATE TABLE {self.name} (\n" + ",\n".join(lines) + "\n);"


@dataclass
class SchemaMetadata:
    tables: dict[str, Table]

    def subset(self, names: list[str]) -> SchemaMetadata:
        return SchemaMetadata({n: self.tables[n] for n in names if n in self.tables})

    def to_prompt(self) -> str:
        return "\n\n".join(t.to_ddl() for t in self.tables.values())

    def column_names(self) -> dict[str, set[str]]:
        return {t.name: {c.name for c in t.columns} for t in self.tables.values()}

    def __len__(self) -> int:
        return len(self.tables)


_COLUMNS_SQL = """
SELECT c.table_name, c.column_name, c.data_type, c.is_nullable = 'YES',
       col_description(format('%%I.%%I', c.table_schema, c.table_name)::regclass, c.ordinal_position)
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = %(schema)s AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position
"""

_TABLE_COMMENTS_SQL = """
SELECT c.relname, obj_description(c.oid, 'pg_class')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %(schema)s AND c.relkind = 'r'
"""

_PK_SQL = """
SELECT tc.table_name, kcu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
WHERE tc.table_schema = %(schema)s AND tc.constraint_type = 'PRIMARY KEY'
ORDER BY tc.table_name, kcu.ordinal_position
"""

_FK_SQL = """
SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
WHERE tc.table_schema = %(schema)s AND tc.constraint_type = 'FOREIGN KEY'
"""


def introspect(conn, schema: str = "public") -> SchemaMetadata:
    """Read schema metadata from a live psycopg connection."""
    params = {"schema": schema}
    tables: dict[str, Table] = {}
    with conn.cursor() as cur:
        cur.execute(_COLUMNS_SQL, params)
        for tname, cname, dtype, nullable, comment in cur.fetchall():
            tables.setdefault(tname, Table(tname)).columns.append(
                Column(cname, dtype, nullable, comment)
            )
        cur.execute(_TABLE_COMMENTS_SQL, params)
        for tname, comment in cur.fetchall():
            if tname in tables:
                tables[tname].comment = comment
        cur.execute(_PK_SQL, params)
        for tname, cname in cur.fetchall():
            if tname in tables:
                tables[tname].primary_key.append(cname)
        cur.execute(_FK_SQL, params)
        for tname, cname, rtable, rcol in cur.fetchall():
            if tname in tables:
                tables[tname].foreign_keys.append(ForeignKey(cname, rtable, rcol))
    return SchemaMetadata(tables)
