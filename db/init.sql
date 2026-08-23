-- Cricket vs Football - Consolidated vote counts
-- Executed automatically by the official postgres image on first container start
-- (mounted into /docker-entrypoint-initdb.d/)

CREATE TABLE IF NOT EXISTS votes (
    candidate   VARCHAR(20) PRIMARY KEY
                CHECK (candidate IN ('cricket', 'football')),
    vote_count  BIGINT NOT NULL DEFAULT 0
                CHECK (vote_count >= 0),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the two candidates so the worker's UPSERT always has a row to update
INSERT INTO votes (candidate, vote_count)
VALUES ('cricket', 0), ('football', 0)
ON CONFLICT (candidate) DO NOTHING;

-- Read-only view convenient for dashboards / BI tools
CREATE OR REPLACE VIEW vote_results AS
SELECT candidate,
       vote_count,
       ROUND(100.0 * vote_count / NULLIF(SUM(vote_count) OVER (), 0), 2) AS pct_share,
       updated_at
FROM votes
ORDER BY vote_count DESC;

-- Least-privilege application role (used by both app-tier read access and worker writes)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'voting_user') THEN
        CREATE ROLE voting_user LOGIN PASSWORD 'changeme_in_secret';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE votingdb TO voting_user;
GRANT SELECT, INSERT, UPDATE ON votes TO voting_user;
GRANT SELECT ON vote_results TO voting_user;
