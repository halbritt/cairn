//! Actually observed inputs and explicit gaps. No caller-supplied identity or ready flag.
use crate::{
    arithmetic::operation,
    assets::{read_owned, AssetPaths},
};
use cairn_semantic_protocol::PyString;
use cairn_semantic_rank::backend::BackendError;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MissingObservation {
    pub field: &'static str,
    pub owner_action: &'static str,
}
/// Partial observed inputs are inspectable, but NEVER published as a complete rank identity.
pub struct ConstructionReport {
    inputs: Value,
    provenance: Vec<PathBuf>,
    missing: Vec<MissingObservation>,
}
impl ConstructionReport {
    pub fn inputs(&self) -> &Value {
        &self.inputs
    }
    pub fn provenance_paths(&self) -> &[PathBuf] {
        &self.provenance
    }
    pub fn missing(&self) -> &[MissingObservation] {
        &self.missing
    }
    pub fn rank_identity(&self) -> Result<PyString, Vec<MissingObservation>> {
        let mut missing = self.missing.clone();
        for gap in required_observations(&self.inputs) {
            if !missing.iter().any(|existing| existing.field == gap.field) {
                missing.push(gap);
            }
        }
        if !missing.is_empty() {
            return Err(missing);
        }
        let bytes = canonical(&self.inputs).map_err(|_| {
            vec![MissingObservation {
                field: "canonical_manifest",
                owner_action: "remove noncanonical float values",
            }]
        })?;
        Ok(
            PyString::from_codepoints(digest(&bytes).chars().map(u32::from).collect())
                .expect("hex scalar string"),
        )
    }
}
fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}
/// Compact UTF-8, recursively sorted keys; integers only, floating constants are decimal strings.
pub fn canonical(value: &Value) -> Result<Vec<u8>, &'static str> {
    fn sorted(value: &Value) -> Result<Value, &'static str> {
        Ok(match value {
            Value::Object(map) => {
                let entries: BTreeMap<_, _> = map
                    .iter()
                    .map(|(k, v)| Ok((k.clone(), sorted(v)?)))
                    .collect::<Result<_, &'static str>>()?;
                Value::Object(entries.into_iter().collect())
            }
            Value::Array(items) => {
                Value::Array(items.iter().map(sorted).collect::<Result<_, _>>()?)
            }
            Value::Number(n) if n.as_i64().is_none() && n.as_u64().is_none() => {
                return Err("floating identity value requires canonical decimal string")
            }
            other => other.clone(),
        })
    }
    serde_json::to_vec(&sorted(value)?).map_err(|_| "canonical serialization failed")
}
fn missing(field: &'static str, owner_action: &'static str) -> MissingObservation {
    MissingObservation {
        field,
        owner_action,
    }
}

fn required_observations(inputs: &Value) -> Vec<MissingObservation> {
    const REQUIRED: &[(&str, &str, &str)] = &[
        ("/compilation/linked_input_attestation", "compilation.linked_input_attestation", "bind complete local+registry source, rustc/target/features, per-unit Rust/C flags and actual link receipts to this observed binary; runtime source snapshot alone is insufficient"),
        ("/session/cpu_arena", "session.cpu_arena", "extend selected API29 bridge/SessionSettings observation; NativeDefault::NotExposedBySelectedCapi cannot become an assumed default"),
        ("/session/remaining_defaults", "session.remaining_defaults", "enumerate and observe vector-affecting native defaults through the ORT owner"),
        ("/session/effective_provider", "session.effective_provider", "extend actual ORT session observation; existing SessionSettings exposes no effective provider field"),
        ("/native/ort_dependency_inventory", "native.ort_dependency_inventory", "extend ORT mapped dependency provenance beyond the existing OrtGetApiBase mapping"),
        ("/native/cpu_kernel_class", "native.cpu_os_complete_class", "bind complete effective ISA/OS vector state and selected kernels to actual library getters; bounded feature observations are partial"),
        ("/assets_model_external_data_closure", "assets.model_external_data_closure", "establish self-contained optimized ONNX/complete actually consumed assets; existing session interface retains model bytes only"),
    ];
    REQUIRED
        .iter()
        .filter_map(|(pointer, field, action)| {
            if inputs.pointer(pointer).is_none_or(Value::is_null) {
                Some(missing(field, action))
            } else {
                None
            }
        })
        .collect()
}

pub(crate) fn observe(
    paths: &AssetPaths,
    tokenizer: &cairn_semantic_tokenizer::PreparedTokenizer,
    ort: &cairn_semantic_ort::Session,
    math: &cairn_semantic_math::Runtime,
) -> Result<ConstructionReport, BackendError> {
    let implementation = source_snapshot(&paths.source_root)?;
    let executable = std::env::current_exe().map_err(|e| operation("observe current binary", e))?;
    let executable_bytes = read_owned(&executable)?;
    let mut provenance = vec![
        paths.model.clone(),
        paths.config.clone(),
        paths.tokenizer_config.clone(),
        paths.special_tokens_map.clone(),
        paths.tokenizer.clone(),
        paths.source_root.clone(),
        executable,
    ];
    let assets: Vec<Value> = ort
        .consumed_tokenizer_files()
        .iter()
        .map(|file| {
            json!({
                "name": file.name, "bytes": file.bytes.len(), "sha256": digest(&file.bytes)
            })
        })
        .chain(std::iter::once(
            json!({ "name": ort.model_provenance().name,
        "bytes": ort.model_bytes().len(), "sha256": digest(ort.model_bytes()) }),
        ))
        .collect();
    let prepared = tokenizer
        .model_json()
        .map_err(|e| operation("observe prepared tokenizer", e))?;
    let rp = ort.runtime_provenance();
    provenance.extend([rp.selected_file.path.clone(), rp.mapped_file.path.clone()]);
    let mp = math.provenance();
    provenance.extend(
        mp.mapped_modules
            .iter()
            .map(|module| module.file.path.clone()),
    );
    let libraries: Vec<Value> = mp.mapped_modules.iter().map(|module| json!({
        "role": format!("{:?}", module.role), "bytes": module.file.bytes, "sha256": hex(&module.file.sha256)
    })).collect();
    let settings = ort.settings();
    let nodes = |items: &[cairn_semantic_ort::NodeMetadata]| -> Vec<Value> {
        items.iter().map(|n| json!({
        "name": n.name, "element_type": format!("{:?}", n.element_type), "dimensions": n.dimensions
    })).collect()
    };
    let cpu = crate::platform::observe_cpu();
    let inputs = json!({
        "tag": "cairn.rust-rank-worker/1",
        "implementation": implementation,
        "compilation": { "binary_sha256": digest(&executable_bytes), "rustc": null,
            "target": null, "enabled_features": null, "effective_flags": null, "linked_input_attestation": null },
        "assets": assets, "assets_model_external_data_closure": null, "prepared_model_tokenizer_sha256": digest(prepared.as_bytes()),
        "native": { "ort_dependency_inventory": null, "cpu_kernel_class": null, "ort": { "version": rp.reported_version, "api": rp.api_version,
            "selected_sha256": hex(&rp.selected_file.sha256), "mapped_sha256": hex(&rp.mapped_file.sha256) },
            "math_modules": libraries, "openblas_config_hex": hex(&mp.settings.config),
            "openblas_core_hex": hex(&mp.settings.core), "threads": mp.settings.threads,
            "parallel": mp.settings.parallel, "cpu_os_observation": cpu },
        "session": { "intra_op_cap": settings.intra_op_threads, "inter_op_cap": settings.inter_op_threads,
            "optimization": settings.graph_optimization, "deterministic": settings.deterministic_compute,
            "profiling": settings.profiling, "execution_mode": settings.execution_mode,
            "memory_pattern": settings.memory_pattern_enabled, "cpu_arena": null, "remaining_defaults": null,
            "effective_provider": null, "batch_size": 1, "inputs": nodes(&ort.metadata().inputs),
            "outputs": nodes(&ort.metadata().outputs) },
        "algorithms": { "prefix": "Represent this sentence for searching relevant passages: ",
            "width": 384, "max_tokens": 512, "window": 384, "stride": 320, "max_passages": 128,
            "pooling": "CLS output[0][:,0]", "first_epsilon_decimal": "0.000000000001",
            "dtype": "float32", "normalizations": ["squares/pairwise128x8/+0/sqrt/maximum-epsilon/divide", "full-occurrence-squares/pairwise128x8/+0/sqrt/all-zero-before-divide"],
            "matvec": ["M0-empty", "M1-sdot384-plus-positive-zero", "M>=2-one-sgemv"],
            "rank": "bge-max-chunk/1", "note_reduce": "ordered-max-from-minus-one/f64-clamp-scale/python-ties-even",
            "cache": "first-pass-bits/exact-occurrences/no-renormalization", "tokenizer_release": "0.23.2",
            "rank_tokenizer": "prepared-serialize-deserialize/no-padding/no-truncation",
            "specials": "rank-default-or-disabled/decode-keeps-special/model-default",
            "model_feed": "batch-one-int64-ids/mask/zero-type-ids" }
    });
    // These precise gaps are properties of existing interfaces, not configurable
    // user assertions. There is deliberately no API to supply a digest/ready flag.
    let missing = required_observations(&inputs);
    Ok(ConstructionReport {
        inputs,
        provenance,
        missing,
    })
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
fn source_snapshot(root: &Path) -> Result<Value, BackendError> {
    const CRATES: &[&str] = &[
        "semantic-backend",
        "semantic-math",
        "semantic-ort",
        "semantic-protocol",
        "semantic-rank",
        "semantic-score",
        "semantic-text",
        "semantic-tokenizer",
    ];
    fn walk(
        root: &Path,
        dir: &Path,
        files: &mut BTreeMap<String, Value>,
    ) -> Result<(), BackendError> {
        for entry in
            std::fs::read_dir(dir).map_err(|e| operation("enumerate implementation", (dir, e)))?
        {
            let entry = entry.map_err(|e| operation("read implementation entry", e))?;
            let path = entry.path();
            let kind = entry
                .file_type()
                .map_err(|e| operation("implementation file type", e))?;
            if kind.is_symlink() {
                return Err(operation("implementation symlink", &path));
            }
            if kind.is_dir() {
                if entry.file_name() != "target" && entry.file_name() != ".git" {
                    walk(root, &path, files)?;
                }
            } else if kind.is_file() {
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if name.ends_with(".rs")
                    || name.ends_with(".c")
                    || name.ends_with(".h")
                    || name == "Cargo.toml"
                    || name == "Cargo.lock"
                    || name.starts_with("rust-toolchain")
                {
                    let relative = path
                        .strip_prefix(root)
                        .map_err(|e| operation("relative implementation", e))?
                        .to_str()
                        .ok_or_else(|| operation("implementation path", "non-UTF8"))?
                        .replace('\\', "/");
                    let bytes = read_owned(&path)?;
                    files.insert(
                        relative,
                        json!({"bytes":bytes.len(),"sha256":digest(&bytes)}),
                    );
                }
            }
        }
        Ok(())
    }
    let mut files = BTreeMap::new();
    for name in CRATES {
        walk(root, &root.join("rust").join(name), &mut files)?;
    }
    for name in ["rust-toolchain", "rust-toolchain.toml"] {
        let path = root.join(name);
        if path
            .try_exists()
            .map_err(|e| operation("toolchain declaration", e))?
        {
            let bytes = read_owned(&path)?;
            files.insert(
                name.into(),
                json!({"bytes":bytes.len(),"sha256":digest(&bytes)}),
            );
        }
    }
    Ok(json!(files))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn backend1_unobserved_provider_defaults_and_build_never_publish_identity() {
        // Test-only partial observation; no native owner/library/CPU probe.
        let report = ConstructionReport {
            inputs: json!({"tag":"cairn.rust-rank-worker/1", "session":{"cpu_arena":null}}),
            provenance: vec![PathBuf::from("/synthetic/moved")],
            missing: vec![missing(
                "session.cpu_arena",
                "actual API observation required",
            )],
        };
        let gap = report.rank_identity().err().unwrap();
        assert_eq!(gap[0].field, "session.cpu_arena");
        assert!(report.inputs().get("session").unwrap()["cpu_arena"].is_null());
        assert_eq!(
            report.provenance_paths(),
            &[PathBuf::from("/synthetic/moved")]
        );
    }
    #[test]
    fn backend1_canonical_keys_features_and_sequences_have_distinct_rules() {
        let a = json!({"z":{"b":2,"a":1},"a":["first","second"],"epsilon":"0.000000000001"});
        let b: Value = serde_json::from_str(
            r#"{"epsilon":"0.000000000001","a":["first","second"],"z":{"a":1,"b":2}}"#,
        )
        .unwrap();
        assert_eq!(canonical(&a).unwrap(), canonical(&b).unwrap());
        assert_eq!(
            canonical(&a).unwrap(),
            br#"{"a":["first","second"],"epsilon":"0.000000000001","z":{"a":1,"b":2}}"#
        );
        assert_ne!(
            canonical(&a).unwrap(),
            canonical(
                &json!({"z":{"a":1,"b":2},"a":["second","first"],"epsilon":"0.000000000001"})
            )
            .unwrap()
        );
        assert!(canonical(&json!({"epsilon":1e-12})).is_err());
        assert_eq!(
            crate::platform::normalized_features(vec!["sse2", "avx2", "sse2"]),
            vec!["avx2", "sse2"]
        );
    }
    #[test]
    fn backend1_observed_input_changes_invalidate_digest_without_install_paths() {
        let inputs = json!({"tag":"cairn.rust-rank-worker/1", "asset":{"sha256":"abc"}, "flags":["-Ctarget-cpu=x86-64"]});
        let original = digest(&canonical(&inputs).unwrap());
        for changed in [
            json!({"tag":"cairn.rust-rank-worker/1", "asset":{"sha256":"def"}, "flags":["-Ctarget-cpu=x86-64"]}),
            json!({"tag":"cairn.rust-rank-worker/1", "asset":{"sha256":"abc"}, "flags":["-Ctarget-cpu=native"]}),
        ] {
            assert_ne!(original, digest(&canonical(&changed).unwrap()));
        }
        assert_eq!(original, digest(&canonical(&inputs.clone()).unwrap()));
        // Absolute install paths/timestamps are report provenance, absent here.
        assert!(!String::from_utf8(canonical(&inputs).unwrap())
            .unwrap()
            .contains("/synthetic"));
    }
}
