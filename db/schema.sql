-- Smart Database Assistant: e-commerce schema (Supabase / PostgreSQL)
-- Tables: categories, suppliers, products, customers, employees, orders,
--         order_items, payments, reviews
-- COMMENT ON statements matter: they are part of the metadata given to the LLM
-- and indexed by schema RAG, so keep them in business language.

DROP TABLE IF EXISTS reviews, payments, order_items, orders, employees,
                     customers, products, suppliers, categories CASCADE;

CREATE TABLE categories (
    category_id   SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT,
    parent_id     INT REFERENCES categories(category_id)
);
COMMENT ON TABLE categories IS 'Product categories (e.g. Electronics, Home). parent_id allows sub-categories.';

CREATE TABLE suppliers (
    supplier_id   SERIAL PRIMARY KEY,
    company_name  TEXT NOT NULL,
    contact_name  TEXT,
    email         TEXT,
    country       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE suppliers IS 'Vendors that supply products to the store.';

CREATE TABLE products (
    product_id    SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    category_id   INT NOT NULL REFERENCES categories(category_id),
    supplier_id   INT REFERENCES suppliers(supplier_id),
    unit_price    NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),
    stock_qty     INT NOT NULL DEFAULT 0,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE products IS 'Items sold in the store. unit_price is the current list price; there is intentionally NO cost column (profit margin is unanswerable).';
COMMENT ON COLUMN products.stock_qty IS 'Units currently in inventory.';

CREATE TABLE customers (
    customer_id   SERIAL PRIMARY KEY,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    email         TEXT UNIQUE,
    city          TEXT,
    state         TEXT,          -- US state name, e.g. 'California'
    country       TEXT NOT NULL DEFAULT 'USA',
    signup_date   DATE NOT NULL,
    loyalty_tier  TEXT CHECK (loyalty_tier IN ('bronze','silver','gold','platinum'))
);
COMMENT ON TABLE customers IS 'People who have an account with the store. signup_date = when they registered.';

CREATE TABLE employees (
    employee_id   SERIAL PRIMARY KEY,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    title         TEXT,
    department    TEXT,
    manager_id    INT REFERENCES employees(employee_id),
    hire_date     DATE NOT NULL,
    salary        NUMERIC(10,2)
);
COMMENT ON TABLE employees IS 'Staff. Sales reps are linked to the orders they handled via orders.employee_id.';

CREATE TABLE orders (
    order_id      SERIAL PRIMARY KEY,
    customer_id   INT NOT NULL REFERENCES customers(customer_id),
    employee_id   INT REFERENCES employees(employee_id),
    order_date    TIMESTAMPTZ NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending','shipped','delivered','cancelled','returned')),
    ship_city     TEXT,
    ship_state    TEXT,
    shipped_date  TIMESTAMPTZ
);
COMMENT ON TABLE orders IS 'One row per customer order. Revenue should normally exclude cancelled orders.';

CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id    INT NOT NULL REFERENCES products(product_id),
    quantity      INT NOT NULL CHECK (quantity > 0),
    unit_price    NUMERIC(10,2) NOT NULL,   -- price at time of sale
    discount      NUMERIC(4,3) NOT NULL DEFAULT 0 CHECK (discount BETWEEN 0 AND 1)
);
COMMENT ON TABLE order_items IS 'Line items of an order. Line revenue = quantity * unit_price * (1 - discount).';

CREATE TABLE payments (
    payment_id    SERIAL PRIMARY KEY,
    order_id      INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    amount        NUMERIC(12,2) NOT NULL,
    method        TEXT NOT NULL CHECK (method IN ('credit_card','debit_card','paypal','gift_card','bank_transfer')),
    paid_at       TIMESTAMPTZ NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('completed','refunded','failed'))
);
COMMENT ON TABLE payments IS 'Payments recorded against orders.';

CREATE TABLE reviews (
    review_id     SERIAL PRIMARY KEY,
    product_id    INT NOT NULL REFERENCES products(product_id),
    customer_id   INT NOT NULL REFERENCES customers(customer_id),
    rating        INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    review_text   TEXT,
    created_at    TIMESTAMPTZ NOT NULL
);
COMMENT ON TABLE reviews IS 'Customer product reviews, 1-5 stars.';

CREATE INDEX ON orders (customer_id);
CREATE INDEX ON orders (order_date);
CREATE INDEX ON order_items (order_id);
CREATE INDEX ON order_items (product_id);
CREATE INDEX ON payments (order_id);
CREATE INDEX ON reviews (product_id);
