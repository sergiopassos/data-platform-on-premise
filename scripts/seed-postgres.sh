#!/usr/bin/env bash
set -euo pipefail

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-sourcedb}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
ROW_COUNT="${1:-100}"

log() { echo "[seed-postgres] $*"; }

export PGPASSWORD="$POSTGRES_PASSWORD"
PSQL="psql -h $POSTGRES_HOST -p $POSTGRES_PORT -U $POSTGRES_USER -d $POSTGRES_DB"

log "Enabling logical replication..."
$PSQL -c "ALTER SYSTEM SET wal_level = logical;" || true
$PSQL -c "SELECT pg_reload_conf();" || true

log "Creating customers table..."
$PSQL << 'SQL'
CREATE TABLE IF NOT EXISTS customers (
    customer_id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
SQL

log "Creating orders table..."
$PSQL << 'SQL'
CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    amount NUMERIC(10,2) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
SQL

log "Inserting $ROW_COUNT customers..."
$PSQL << SQL
INSERT INTO customers (email, name)
SELECT
    'customer_' || i || '@example.com',
    'Customer ' || i
FROM generate_series(1, $ROW_COUNT) AS i
ON CONFLICT DO NOTHING;
SQL

log "Inserting $ROW_COUNT orders..."
$PSQL << SQL
INSERT INTO orders (customer_id, status, amount)
SELECT
    (i % $ROW_COUNT) + 1,
    CASE (i % 5)
        WHEN 0 THEN 'pending'
        WHEN 1 THEN 'processing'
        WHEN 2 THEN 'shipped'
        WHEN 3 THEN 'delivered'
        ELSE 'cancelled'
    END,
    (RANDOM() * 1000)::NUMERIC(10,2)
FROM generate_series(1, $ROW_COUNT) AS i;
SQL

log "Seed complete: $ROW_COUNT customers + $ROW_COUNT orders."
