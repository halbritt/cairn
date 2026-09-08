CREATE TABLE cairn.run_retrieval (
 retrieval_receipt_id uuid PRIMARY KEY REFERENCES cairn.retrieval_receipt(receipt_id),
 run_receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
 reader text NOT NULL,
 observer text NOT NULL DEFAULT current_setting('cairn.caller'),
 method text NOT NULL CHECK(length(btrim(method)) BETWEEN 1 AND 256),
 observed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 CHECK(retrieval_receipt_id <> run_receipt_id)
);
CREATE INDEX run_retrieval_run ON cairn.run_retrieval(run_receipt_id);
