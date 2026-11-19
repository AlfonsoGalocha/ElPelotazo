BEGIN;
CREATE TABLE IF NOT EXISTS fixture_publications (
    match_id integer PRIMARY KEY REFERENCES matches(id),
    captured_at timestamp NOT NULL,
    scheduled_kickoff timestamp NOT NULL,
    ranking_version varchar(32) NOT NULL,
    groups json NOT NULL,
    ranking json NOT NULL,
    recommendation json,
    CONSTRAINT ck_publication_pre_kickoff CHECK (captured_at < scheduled_kickoff)
);
DROP TRIGGER IF EXISTS historical_append_only ON fixture_publications;
CREATE TRIGGER historical_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON fixture_publications
FOR EACH STATEMENT EXECUTE FUNCTION prevent_historical_rewrite();
CREATE TABLE IF NOT EXISTS fixture_ranking_revisions (
    match_id integer NOT NULL REFERENCES matches(id),
    captured_at timestamp NOT NULL,
    scheduled_kickoff timestamp NOT NULL,
    ranking_version varchar(32) NOT NULL,
    ranking json NOT NULL,
    PRIMARY KEY (match_id, captured_at),
    CONSTRAINT ck_revision_pre_kickoff CHECK (captured_at < scheduled_kickoff)
);
DROP TRIGGER IF EXISTS historical_append_only ON fixture_ranking_revisions;
CREATE TRIGGER historical_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON fixture_ranking_revisions
FOR EACH STATEMENT EXECUTE FUNCTION prevent_historical_rewrite();
COMMIT;
