-- V3: Owner identity mapping
--
-- Portfolio-lots plan (issue #126), PR 2, Step 2.1. Maps an authenticated
-- identity (IAP email for ES256 UI tokens, or MCP token `sub` for HS256
-- tokens) to the canonical short-handle `owner` value already used as the
-- literal in `positions.owner` (e.g. 'john'). Additive only — no DROP.
--
-- Unknown identities are never auto-provisioned (issue #126 decision #2):
-- rows are added by an admin, via scripts/grant_quantui_iap_access.sh going
-- forward. The seed inserts below cover the users already granted IAP access
-- as of this migration.
CREATE TABLE IF NOT EXISTS owner_identities (
    identity   TEXT PRIMARY KEY,      -- IAP email OR MCP token sub, lowercased
    owner      TEXT NOT NULL,         -- canonical short handle, e.g. 'john'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes      TEXT
);

CREATE INDEX IF NOT EXISTS idx_owner_identities_owner ON owner_identities(owner);

-- Seed: existing provisioned users (identity lowercased). ON CONFLICT DO
-- NOTHING keeps this migration re-runnable and safe to apply after manual
-- rows have already been added.
INSERT INTO owner_identities (identity, owner, notes) VALUES
    ('john', 'john', 'MCP token default sub'),
    ('funkjohn@gmail.com', 'john', 'IAP email'),
    ('john@johnfunk.com', 'john', 'IAP email'),
    ('thomas@zoidbergfolio.com', 'thomas', 'IAP email'),
    ('dr.sagerjl@gmail.com', 'sager', 'IAP email'),
    ('musicalmacdonald@gmail.com', 'macdonald', 'IAP email'),
    ('superdavidabrown@gmail.com', 'dabrown', 'IAP email')
ON CONFLICT (identity) DO NOTHING;
