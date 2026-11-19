-- Workstream status board (CAIRN-466): latest bounded status per stream with
-- owner/writer authorization and retained revision history; setting a status
-- emits no events and wakes no session.

CREATE TABLE cairn.workstream_status (
	repo text NOT NULL CHECK (octet_length(btrim(repo)) BETWEEN 1 AND 256 AND repo <> '*'),
	stream text NOT NULL CHECK (stream ~ '^[A-Za-z0-9_.-]{1,64}$'),
	owner text NOT NULL CHECK (octet_length(owner) BETWEEN 1 AND 256),
	authorized_writers text[] NOT NULL DEFAULT '{}' CHECK (cardinality(authorized_writers) <= 16 AND array_position(authorized_writers, NULL) IS NULL),
	writer text NOT NULL CHECK (octet_length(writer) BETWEEN 1 AND 256),
	revision bigint NOT NULL CHECK (revision >= 1),
	state text NOT NULL CHECK (state IN ('active', 'waiting', 'blocked', 'done')),
	current_item text NOT NULL DEFAULT '',
	last_landed text NOT NULL DEFAULT '',
	next_checkpoint timestamptz,
	blocker text NOT NULL DEFAULT '',
	decision_needed text NOT NULL DEFAULT '',
	detail text NOT NULL DEFAULT '',
	sensitivity text NOT NULL DEFAULT 'shareable' CHECK (sensitivity IN ('local', 'shareable')),
	entry_bytes int NOT NULL CHECK (entry_bytes >= 0 AND entry_bytes <= 2048),
	created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
	updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
	PRIMARY KEY (repo, stream),
	CONSTRAINT workstream_status_fields_bounded CHECK (
		octet_length(current_item) <= 512
		AND octet_length(last_landed) <= 512
		AND octet_length(blocker) <= 512
		AND octet_length(decision_needed) <= 512
		AND octet_length(detail) <= 1024
		AND (
			octet_length(stream)
			+ octet_length(state)
			+ octet_length(current_item)
			+ octet_length(last_landed)
			+ octet_length(blocker)
			+ octet_length(decision_needed)
			+ octet_length(detail)
		) = entry_bytes
	)
);

CREATE INDEX workstream_status_checkpoint_idx
	ON cairn.workstream_status(repo, next_checkpoint)
	WHERE next_checkpoint IS NOT NULL AND state <> 'done';

CREATE TABLE cairn.workstream_status_history (
	repo text NOT NULL CHECK (octet_length(btrim(repo)) BETWEEN 1 AND 256 AND repo <> '*'),
	stream text NOT NULL CHECK (stream ~ '^[A-Za-z0-9_.-]{1,64}$'),
	revision bigint NOT NULL CHECK (revision >= 1),
	owner text NOT NULL CHECK (octet_length(owner) BETWEEN 1 AND 256),
	writer text NOT NULL CHECK (octet_length(writer) BETWEEN 1 AND 256),
	state text NOT NULL CHECK (state IN ('active', 'waiting', 'blocked', 'done')),
	current_item text NOT NULL DEFAULT '',
	last_landed text NOT NULL DEFAULT '',
	next_checkpoint timestamptz,
	blocker text NOT NULL DEFAULT '',
	decision_needed text NOT NULL DEFAULT '',
	detail text NOT NULL DEFAULT '',
	sensitivity text NOT NULL DEFAULT 'shareable' CHECK (sensitivity IN ('local', 'shareable')),
	entry_bytes int NOT NULL CHECK (entry_bytes >= 0 AND entry_bytes <= 2048),
	written_at timestamptz NOT NULL,
	PRIMARY KEY (repo, stream, revision),
	FOREIGN KEY (repo, stream) REFERENCES cairn.workstream_status(repo, stream) ON DELETE CASCADE,
	CONSTRAINT workstream_status_history_fields_bounded CHECK (
		octet_length(current_item) <= 512
		AND octet_length(last_landed) <= 512
		AND octet_length(blocker) <= 512
		AND octet_length(decision_needed) <= 512
		AND octet_length(detail) <= 1024
		AND (
			octet_length(stream)
			+ octet_length(state)
			+ octet_length(current_item)
			+ octet_length(last_landed)
			+ octet_length(blocker)
			+ octet_length(decision_needed)
			+ octet_length(detail)
		) = entry_bytes
	)
);
