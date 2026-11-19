"""Create the e-commerce schema and load deterministic synthetic data.

Usage:
    python db/seed.py                 # uses DATABASE_ADMIN_URL from .env
    python db/seed.py --scale 2       # 2x rows

The RNG is seeded, so every teammate gets identical data and the gold set's
expected rows stay stable. If you change this file, re-run the gold set
verification (`python -m smart_db_assistant.eval.verify_gold`).
"""

from __future__ import annotations

import argparse
import os
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from faker import Faker

SEED = 42
ROOT = Path(__file__).resolve().parent

CATEGORIES = [
    ("Electronics", "Phones, laptops, accessories"),
    ("Home & Kitchen", "Appliances, cookware, decor"),
    ("Books", "Print and e-books"),
    ("Clothing", "Apparel for all ages"),
    ("Sports & Outdoors", "Fitness and outdoor gear"),
    ("Beauty", "Skincare and cosmetics"),
    ("Toys & Games", "Kids toys and board games"),
    ("Grocery", "Packaged food and beverages"),
]
STATUSES = ["delivered"] * 70 + ["shipped"] * 10 + ["pending"] * 5 + ["cancelled"] * 10 + ["returned"] * 5
METHODS = ["credit_card", "debit_card", "paypal", "gift_card", "bank_transfer"]
TIERS = ["bronze", "silver", "gold", "platinum", None]
DEPARTMENTS = ["Sales", "Support", "Operations", "Marketing", "Finance"]
START = date(2023, 1, 1)
END = date(2026, 10, 31)


def rand_date(rng: random.Random, start: date = START, end: date = END) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def rand_ts(rng: random.Random, start: date = START, end: date = END) -> datetime:
    d = rand_date(rng, start, end)
    return datetime(d.year, d.month, d.day, rng.randint(0, 23), rng.randint(0, 59), tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("DATABASE_ADMIN_URL") or os.environ["DATABASE_URL"]
    rng = random.Random(SEED)
    fake = Faker("en_US")
    Faker.seed(SEED)

    n_suppliers = int(25 * args.scale)
    n_products = int(200 * args.scale)
    n_customers = int(2000 * args.scale)
    n_employees = int(40 * args.scale)
    n_orders = int(10000 * args.scale)
    n_reviews = int(4000 * args.scale)

    with psycopg.connect(url, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute((ROOT / "schema.sql").read_text())

        cur.executemany(
            "INSERT INTO categories (name, description) VALUES (%s, %s)", CATEGORIES
        )
        cur.executemany(
            "INSERT INTO suppliers (company_name, contact_name, email, country, created_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            [
                (fake.company(), fake.name(), fake.company_email(),
                 rng.choice(["USA", "China", "Germany", "India", "Mexico", "Vietnam"]),
                 rand_ts(rng, START, date(2023, 6, 1)))
                for _ in range(n_suppliers)
            ],
        )

        products = []
        for i in range(n_products):
            price = round(rng.lognormvariate(3.3, 0.9), 2)
            products.append((
                f"{fake.word().title()} {fake.word().title()} {i + 1}",
                rng.randint(1, len(CATEGORIES)),
                rng.randint(1, n_suppliers),
                max(price, 1.99),
                rng.randint(0, 500),
                rng.random() > 0.08,
                rand_ts(rng, START, date(2025, 6, 1)),
            ))
        cur.executemany(
            "INSERT INTO products (name, category_id, supplier_id, unit_price, stock_qty, is_active, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            products,
        )
        prices = {i + 1: p[3] for i, p in enumerate(products)}

        customers = []
        for i in range(n_customers):
            customers.append((
                fake.first_name(), fake.last_name(), f"user{i + 1}@example.com",
                fake.city(), fake.state(), "USA", rand_date(rng), rng.choice(TIERS),
            ))
        cur.executemany(
            "INSERT INTO customers (first_name, last_name, email, city, state, country, signup_date, loyalty_tier)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            customers,
        )
        signup = {i + 1: c[6] for i, c in enumerate(customers)}

        employees = []
        for i in range(n_employees):
            dept = rng.choice(DEPARTMENTS)
            employees.append((
                fake.first_name(), fake.last_name(),
                "Manager" if i < 5 else f"{dept} Associate", dept,
                None if i < 5 else rng.randint(1, 5),
                rand_date(rng, date(2018, 1, 1), date(2025, 1, 1)),
                round(rng.uniform(45000, 140000), 2),
            ))
        cur.executemany(
            "INSERT INTO employees (first_name, last_name, title, department, manager_id, hire_date, salary)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            employees,
        )

        for order_id in range(1, n_orders + 1):
            cid = rng.randint(1, n_customers)
            odt = rand_ts(rng, signup[cid], END)
            status = rng.choice(STATUSES)
            shipped = odt + timedelta(days=rng.randint(1, 6)) if status in ("shipped", "delivered", "returned") else None
            cur.execute(
                "INSERT INTO orders (customer_id, employee_id, order_date, status, ship_city, ship_state, shipped_date)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (cid, rng.randint(1, n_employees), odt, status, fake.city(), fake.state(), shipped),
            )
            total = 0.0
            items = []
            for pid in rng.sample(range(1, n_products + 1), rng.randint(1, 4)):
                qty = rng.randint(1, 5)
                disc = rng.choice([0, 0, 0, 0.05, 0.1, 0.2])
                items.append((order_id, pid, qty, prices[pid], disc))
                total += qty * float(prices[pid]) * (1 - disc)
            cur.executemany(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price, discount)"
                " VALUES (%s, %s, %s, %s, %s)",
                items,
            )
            if status != "pending":
                pstatus = {"cancelled": "refunded", "returned": "refunded"}.get(status, "completed")
                cur.execute(
                    "INSERT INTO payments (order_id, amount, method, paid_at, status)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (order_id, round(total, 2), rng.choice(METHODS), odt + timedelta(minutes=5), pstatus),
                )

        cur.executemany(
            "INSERT INTO reviews (product_id, customer_id, rating, review_text, created_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            [
                (rng.randint(1, n_products), rng.randint(1, n_customers),
                 rng.choices([1, 2, 3, 4, 5], weights=[5, 7, 15, 35, 38])[0],
                 fake.sentence(nb_words=12) if rng.random() > 0.2 else None,
                 rand_ts(rng))
                for _ in range(n_reviews)
            ],
        )
        conn.commit()
    print(f"Seeded database (scale={args.scale}).")


if __name__ == "__main__":
    main()
