-- Store owner descriptions and free-text swarm roles on the existing team rows.
UPDATE cairn.restore_admission SET paused=paused WHERE singleton;

-- PostgreSQL text already rejects NUL. The role check excludes the remaining
-- Unicode control characters and the Unicode line and paragraph separators.
ALTER TABLE cairn.agent_team_campaign
    ADD COLUMN description text CHECK (octet_length(description) BETWEEN 1 AND 8192);

ALTER TABLE cairn.agent_team_member
    ADD COLUMN role_label text CHECK (
        octet_length(role_label) BETWEEN 1 AND 160
        AND role_label COLLATE "C" !~ U&'[\0001-\001F\007F-\009F\2028\2029]'
    );
