-- Deposit ledger reference schema (PostgreSQL 13+)
-- All identifiers and values are fictional.

CREATE TABLE deposit_ledger (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id    uuid        NOT NULL,
    lease_code    text        NOT NULL,
    amount_cents  bigint      NOT NULL CHECK (amount_cents >= 0),
    currency      char(3)     NOT NULL DEFAULT 'CNY',
    meta          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz
);

-- Only live rows participate: a soft-deleted (account, lease) pair may be reused.
CREATE UNIQUE INDEX uq_deposit_active_lease
    ON deposit_ledger (account_id, lease_code)
    WHERE deleted_at IS NULL;

CREATE INDEX ix_deposit_account ON deposit_ledger (account_id);
