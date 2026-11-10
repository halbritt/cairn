//! Owned mode projection of the same report; no observation or ready constructor.
use super::{
    canonical, digest, missing, required_observations, ConstructionReport, MissingObservation,
};
use cairn_semantic_protocol::PyString;
use serde_json::{json, Value};

impl ConstructionReport {
    /// Rule C retains unavailable bit-identity observations by name. Invalid
    /// observed data and the model-asset closure still refuse initialization.
    /// The strict rank derivation is unchanged.
    pub fn embedding_manifest(&self) -> Result<Value, Vec<MissingObservation>> {
        let unobserved = self.embedding_unobserved_observations()?;
        canonical(&self.inputs).map_err(|_| noncanonical())?;
        let mut manifest = mode_manifest(&self.inputs)?;
        if !unobserved.is_empty() {
            manifest["unobserved_observations"] = json!(unobserved);
        }
        Ok(manifest)
    }

    fn embedding_unobserved_observations(
        &self,
    ) -> Result<Vec<&'static str>, Vec<MissingObservation>> {
        let mut unobserved: Vec<_> = RULE_C_OBSERVATIONS
            .iter()
            .filter_map(|(pointer, field)| {
                let (parent, key) = pointer.rsplit_once('/').expect("observation pointer");
                self.inputs
                    .pointer(parent)
                    .and_then(Value::as_object)
                    .filter(|fields| fields.get(key).map_or(true, Value::is_null))
                    .map(|_| *field)
            })
            .collect();
        unobserved.sort_unstable();
        let mut errors = self.missing.clone();
        for gap in required_observations(&self.inputs) {
            if !errors.iter().any(|existing| existing.field == gap.field) {
                errors.push(gap);
            }
        }
        errors.retain(|gap| !unobserved.contains(&gap.field));
        if errors.is_empty() {
            Ok(unobserved)
        } else {
            Err(errors)
        }
    }

    pub fn embedding_identity(&self) -> Result<PyString, Vec<MissingObservation>> {
        let manifest = self.embedding_manifest()?;
        let bytes = canonical(&manifest).map_err(|_| noncanonical())?;
        Ok(PyString::from(digest(&bytes).as_str()))
    }
}

// These ten observation gaps no longer gate embedding initialization under
// rule C. Missing/null leaves stay intact; no default or partial value fills them.
const RULE_C_OBSERVATIONS: &[(&str, &str)] = &[
    ("/compilation/rustc", "compilation.rustc"),
    ("/compilation/target", "compilation.target"),
    (
        "/compilation/enabled_features",
        "compilation.enabled_features",
    ),
    (
        "/compilation/effective_flags",
        "compilation.effective_flags",
    ),
    (
        "/compilation/linked_input_attestation",
        "compilation.linked_input_attestation",
    ),
    ("/session/cpu_arena", "session.cpu_arena"),
    ("/session/remaining_defaults", "session.remaining_defaults"),
    ("/session/effective_provider", "session.effective_provider"),
    (
        "/native/ort_dependency_inventory",
        "native.ort_dependency_inventory",
    ),
    ("/native/cpu_kernel_class", "native.cpu_os_complete_class"),
];

fn noncanonical() -> Vec<MissingObservation> {
    vec![missing(
        "canonical_manifest",
        "remove noncanonical float values",
    )]
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
