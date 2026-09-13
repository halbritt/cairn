CREATE TABLE cairn.task_state (
 observer text NOT NULL DEFAULT current_setting('cairn.caller'),
 repo text NOT NULL,
 task_id text NOT NULL,
 version integer NOT NULL CHECK(version>0),
 run_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('completed','cancelled','reopened')),
 method text NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(observer,repo,task_id,version)
);
