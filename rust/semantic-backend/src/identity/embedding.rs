//! Owned mode projection of the same report; no observation or ready constructor.
use super::{canonical, digest, missing, noncanonical, ConstructionReport, MissingObservation};
use cairn_semantic_protocol::PyString;
use serde_json::{json, Value};

impl ConstructionReport {
    /// Applies the common rule-C observation policy before the embedding-only
    /// projection; rank algorithm fields never come from this projection.
    pub fn embedding_manifest(&self) -> Result<Value, Vec<MissingObservation>> {
        mode_manifest(&self.rank_manifest()?)
    }

    pub fn embedding_identity(&self) -> Result<PyString, Vec<MissingObservation>> {
        let manifest = self.embedding_manifest()?;
        let bytes = canonical(&manifest).map_err(|_| noncanonical())?;
        Ok(PyString::from(digest(&bytes).as_str()))
    }
}

fn mode_manifest(inputs: &Value) -> Result<Value, Vec<MissingObservation>> {
    let mut projected = inputs.clone();
    let root = projected
        .as_object_mut()
        .ok_or_else(incomplete_algorithms)?;
    root.insert("tag".into(), json!("cairn.rust-embedding-worker/1"));
    let algorithms = root
        .get_mut("algorithms")
        .and_then(Value::as_object_mut)
        .ok_or_else(incomplete_algorithms)?;
    let first = algorithms
        .get("normalizations")
        .and_then(Value::as_array)
        .and_then(|rows| rows.first())
        .and_then(Value::as_str)
        .ok_or_else(incomplete_algorithms)?
        .to_owned();
    // Keep all common observed material, including unknown future common
    // fields. Remove only the explicitly rank-exclusive algorithm declarations.
    for key in [
        "window",
        "stride",
        "max_passages",
        "matvec",
        "rank",
        "note_reduce",
        "cache",
    ] {
        algorithms.remove(key);
    }
    algorithms.insert("normalizations".into(), json!([first]));
    algorithms.insert("embedding".into(), json!("bge-original-passages/1"));
    algorithms.insert("dimensions".into(), json!(384));
    algorithms.insert(
        "specials".into(),
        json!("query-default/document-body-disabled/passage-default/model-default"),
    );
    algorithms.insert("query_projection".into(), json!({
        "max_utf8_bytes":4096,
        "max_tokens":512,
        "method":"original-prefix/1",
        "max_retreats":8,
        "initial_end":511,
        "reserve_final_special":true,
        "offsets":"original-Python-characters/nonempty-max-stop-minus-instruction-length",
        "prefix":"strict-positive-proper-original-character-prefix",
        "reencode":"exact-instruction-plus-original-prefix/default-specials",
        "acceptance":"Python-nonblank-and-at-most-512-tokens",
        "retreat":"end-=max(1,embedded_tokens-512);Python-signed-slice-end",
        "metadata":"full-original-and-embedded-counts/strict-UTF8-prefix-bytes/SHA256-excludes-instruction"
    }));
    algorithms.insert(
        "document_projection".into(),
        json!({
            "max_utf8_bytes":65536,
            "window":128,
            "stride":96,
            "adaptive_stride":"max(96,ceil(offsets.len/256))",
            "adaptive_window":"max(128,stride+32)",
            "max_passages":256,
            "final_short_tail":true,
            "offsets":"original-Python-characters/first-and-last-before-clipping",
            "spans":"original-UTF8-prefix-offset/selected-original-UTF8-length",
            "validation":"all-spans/count-before-ordered-default-special-reencode",
            "passage_max_tokens":512,
            "instruction_prefix":false
        }),
    );
    algorithms.insert(
        "vector_validation".into(),
        json!({"dimensions":384,"finite":true,"nonzero":true}),
    );
    Ok(projected)
}

fn incomplete_algorithms() -> Vec<MissingObservation> {
    vec![missing(
        "algorithms.embedding_common",
        "retain the owner-observed common algorithm object and first normalization order",
    )]
}

#[cfg(test)]
mod tests;
