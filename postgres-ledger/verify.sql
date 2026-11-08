-- Smoke verification for the reference schema. Run against a fresh database.
\set ON_ERROR_STOP on

INSERT INTO deposit_ledger (account_id, lease_code, amount_cents, meta) VALUES
    ('11111111-1111-1111-1111-111111111111', 'L-2026-001', 125050, '{"source":"fictional"}'),
    ('11111111-1111-1111-1111-111111111111', 'L-2026-002',  98000, '{"source":"fictional"}');

-- 1) money stored as integer cents, rendered as currency; jsonb field is queryable
SELECT lease_code,
       amount_cents,
       (amount_cents::numeric / 100)::text || ' ' || trim(currency) AS amount,
       meta ->> 'source' AS source
FROM deposit_ledger
WHERE deleted_at IS NULL
ORDER BY lease_code;

-- 2) CHECK constraint rejects negative amounts (caught inside a DO block)
DO $$
BEGIN
    INSERT INTO deposit_ledger (account_id, lease_code, amount_cents)
    VALUES ('11111111-1111-1111-1111-111111111111', 'L-BAD', -1);
    RAISE EXCEPTION 'CHECK constraint did not fire';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'CHECK (amount_cents >= 0) enforced as expected';
END $$;

-- 3) partial unique index: soft-delete then reuse the same business key
UPDATE deposit_ledger SET deleted_at = now() WHERE lease_code = 'L-2026-001';
INSERT INTO deposit_ledger (account_id, lease_code, amount_cents, meta)
VALUES ('11111111-1111-1111-1111-111111111111', 'L-2026-001', 131000, '{"source":"reissued"}');

SELECT lease_code, amount_cents, (deleted_at IS NULL) AS live
FROM deposit_ledger
ORDER BY lease_code, created_at;

-- 4) confirm the partial index is really partial
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'deposit_ledger'
ORDER BY indexname;
