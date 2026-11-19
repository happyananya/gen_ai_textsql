-- Read-only role used by the assistant. Run once as the postgres/admin user.
-- Defense in depth: SQL is also validated (sqlglot) and run inside a READ ONLY
-- transaction, but the DB role is the guarantee.

-- Replace the password before running.
CREATE ROLE sda_readonly LOGIN PASSWORD 'change-me';
GRANT USAGE ON SCHEMA public TO sda_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sda_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO sda_readonly;
ALTER ROLE sda_readonly SET statement_timeout = '10s';
ALTER ROLE sda_readonly SET default_transaction_read_only = on;
