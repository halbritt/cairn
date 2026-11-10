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

mod embedding;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MissingObservation {
    pub field: &'static str,
    pub owner_action: &'static str,
}
/// Partial observed inputs are inspectable, but NEVER published as a complete rank identity.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RunningImageProvenance {
    pub device: u64,
    pub inode: u64,
    pub bytes: usize,
    pub sha256: String,
}
pub struct ConstructionReport {
    inputs: Value,
    provenance: Vec<PathBuf>,
    missing: Vec<MissingObservation>,
    running_image: Option<RunningImageProvenance>,
}
impl ConstructionReport {
    pub fn running_image(&self) -> Option<&RunningImageProvenance> {
        self.running_image.as_ref()
    }
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
        ("/compilation/rustc", "compilation.rustc", "consume Release binary-bound actual rustc observation"),
        ("/compilation/target", "compilation.target", "consume Release binary-bound exact target triple"),
        ("/compilation/enabled_features", "compilation.enabled_features", "consume Release binary-bound per-unit feature sets"),
        ("/compilation/effective_flags", "compilation.effective_flags", "consume Release binary-bound actual Rust/C effective flags"),
        ("/compilation/linked_input_attestation", "compilation.linked_input_attestation", "bind complete local+registry source, rustc/target/features, per-unit Rust/C flags and actual link receipts to this observed binary; runtime source snapshot alone is insufficient"),
        ("/session/cpu_arena", "session.cpu_arena", "B1 must establish actual global arena-enabled evidence; I/O MemoryInfo allocator type is not this session option"),
        ("/session/remaining_defaults", "session.remaining_defaults", "B1 must establish remaining effective vector-affecting defaults; options config entries and existing two getters are only partial"),
        ("/session/effective_provider", "session.effective_provider", "B1 must establish complete final provider/node dispatch evidence; assignment-stage snapshots, optional I/O EpDevice names and internal C++ getters do not certify it"),
        ("/native/ort_dependency_inventory", "native.ort_dependency_inventory", "extend ORT mapped dependency provenance beyond the existing OrtGetApiBase mapping"),
        ("/native/cpu_kernel_class", "native.cpu_os_complete_class", "bind complete effective ISA/OS vector state and selected kernels to actual library getters; bounded feature observations are partial"),
        ("/assets_model_external_data_closure", "assets.model_external_data_closure", "establish self-contained optimized ONNX/complete actually consumed assets; existing session interface retains model bytes only"),
    ];
    REQUIRED
        .iter()
        .filter_map(|(pointer, field, action)| {
            if !required_shape(pointer, inputs.pointer(pointer), inputs) {
                Some(missing(field, action))
            } else {
                None
            }
        })
        .collect()
}

fn nonempty_text(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|s| !s.is_empty())
}
fn sorted_set(value: &Value) -> bool {
    value.as_array().is_some_and(|items| {
        items.iter().all(|v| v.as_str().is_some())
            && items.windows(2).all(|w| w[0].as_str() < w[1].as_str())
    })
}
fn sha256_text(value: Option<&Value>) -> bool {
    value.and_then(Value::as_str).is_some_and(|s| {
        s.len() == 64
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
fn required_shape(pointer: &str, value: Option<&Value>, inputs: &Value) -> bool {
    let Some(value) = value.filter(|v| !v.is_null()) else {
        return false;
    };
    match pointer {
        "/compilation/rustc" | "/compilation/target" | "/session/effective_provider" => {
            nonempty_text(Some(value))
        }
        "/compilation/enabled_features" => value
            .as_object()
            .is_some_and(|units| !units.is_empty() && units.values().all(sorted_set)),
        "/compilation/effective_flags" => value.as_object().is_some_and(|units| {
            !units.is_empty()
                && units.values().all(|v| {
                    v.as_array()
                        .is_some_and(|a| a.iter().all(|v| v.as_str().is_some()))
                })
        }),
        "/compilation/linked_input_attestation" => value.as_object().is_some_and(|record| {
            sha256_text(record.get("binary_sha256"))
                && record.get("binary_sha256") == inputs.pointer("/compilation/binary_sha256")
                && record.get("source_inputs").is_some_and(|v| {
                    v.as_array().is_some_and(|a| {
                        !a.is_empty()
                            && a.iter().all(|entry| {
                                nonempty_text(entry.get("path")) && sha256_text(entry.get("sha256"))
                            })
                    })
                })
        }),
        "/session/cpu_arena" => value.is_boolean(),
        "/session/remaining_defaults" => value
            .as_object()
            .is_some_and(|v| !v.is_empty() && v.values().all(|v| !v.is_null())),
        "/native/ort_dependency_inventory" => value
            .as_array()
            .is_some_and(|v| !v.is_empty() && v.iter().all(|v| sha256_text(v.get("sha256")))),
        "/native/cpu_kernel_class" => value.as_object().is_some_and(|v| {
            sha256_text(v.get("sha256")) && v.get("observed_inputs").is_some_and(Value::is_object)
        }),
        "/assets_model_external_data_closure" => {
            value.get("kind").and_then(Value::as_str)
                == Some("ONNX-v1.22.0-all-TensorProto-paths-self-contained")
                && value
                    .get("model_sha256")
                    .and_then(Value::as_str)
                    .is_some_and(|s| s.len() == 64)
        }
        _ => false,
    }
}

pub(crate) fn observe(
    paths: &AssetPaths,
    tokenizer: &cairn_semantic_tokenizer::PreparedTokenizer,
    ort: &cairn_semantic_ort::Session,
    math: &cairn_semantic_math::Runtime,
    closure: &crate::model_assets::Closure,
) -> Result<ConstructionReport, BackendError> {
    let implementation = source_snapshot(&paths.source_root)?;
    let executable = std::env::current_exe().map_err(|e| operation("observe current binary", e))?;
    let executable_observation = crate::running_image::observe()?;
    let executable_bytes = &executable_observation.bytes;
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
    let cpu = crate::platform::observe_cpu();
    let inputs = json!({
        "tag": "cairn.rust-rank-worker/1",
        "implementation": implementation,
        "compilation": { "binary_sha256": digest(executable_bytes), "rustc": null,
            "target": null, "enabled_features": null, "effective_flags": null, "linked_input_attestation": null },
        "assets": assets, "assets_model_external_data_closure": closure.bind(ort.model_bytes()).map_err(|e| operation("model closure binding",e))?, "prepared_model_tokenizer_sha256": digest(prepared.as_bytes()),
        "native": { "ort_dependency_inventory": null, "cpu_kernel_class": null, "ort": { "version": rp.reported_version, "api": rp.api_version,
            "selected_sha256": hex(&rp.selected_file.sha256), "mapped_sha256": hex(&rp.mapped_file.sha256) },
            "math_modules": libraries, "openblas_config_hex": hex(&mp.settings.config),
            "openblas_core_hex": hex(&mp.settings.core), "threads": mp.settings.threads,
            "parallel": mp.settings.parallel, "cpu_os_observation": cpu },
        "session": session_inputs(settings, ort.metadata(), session_observations_json(ort.observations())),
        "algorithms": { "prefix": "Represent this sentence for searching relevant passages: ",
            "width": 384, "max_tokens": 512, "window": 384, "stride": 320, "max_passages": 128,
            "pooling": "ORT metadata-ordered run(None) result[0]; rank3 CLS[:,0],rank2 direct", "first_epsilon_decimal": "0.000000000001",
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
    let running_image = Some(RunningImageProvenance {
        device: executable_observation.device,
        inode: executable_observation.inode,
        bytes: executable_bytes.len(),
        sha256: digest(executable_bytes),
    });
    Ok(ConstructionReport {
        inputs,
        provenance,
        missing,
        running_image,
    })
}
// Private read-only seam: production forwards owner-constructed ORT records;
// cfg(test) fixtures supply owned values without constructing ORT observations.
trait ConfigEntrySource {
    fn key(&self) -> &[u8];
    fn value(&self) -> &[u8];
}
trait MemoryObservationSource {
    fn name(&self) -> &[u8];
    fn device_type(&self) -> i32;
    fn device_id(&self) -> i32;
    fn allocator_type(&self) -> i32;
    fn memory_type(&self) -> i32;
    fn device_memory_type(&self) -> i32;
    fn vendor_id(&self) -> u32;
}
trait EpDeviceSource {
    fn name(&self) -> Option<&[u8]>;
    fn vendor(&self) -> Option<&[u8]>;
}
trait IoObservationSource {
    type Memory: MemoryObservationSource;
    type EpDevice: EpDeviceSource;
    fn ordinal(&self) -> usize;
    fn node_name(&self) -> &str;
    fn memory(&self) -> Option<&Self::Memory>;
    fn ep_device(&self) -> Option<&Self::EpDevice>;
}
trait AssignedNodeSource {
    fn name(&self) -> &[u8];
    fn domain(&self) -> &[u8];
    fn operator_type(&self) -> &[u8];
}
trait AssignedSubgraphSource {
    type Node: AssignedNodeSource;
    fn provider(&self) -> &[u8];
    fn nodes(&self) -> &[Self::Node];
}
trait GraphAssignmentSource {
    type Subgraph: AssignedSubgraphSource;
    fn subgraphs(&self) -> &[Self::Subgraph];
}
trait SessionObservationSource {
    type Entry: ConfigEntrySource;
    type Io: IoObservationSource;
    type Assignment: GraphAssignmentSource;
    fn options_config_entries(&self) -> &[Self::Entry];
    fn inputs(&self) -> &[Self::Io];
    fn outputs(&self) -> &[Self::Io];
    fn graph_assignment(&self) -> &Self::Assignment;
}

impl ConfigEntrySource for cairn_semantic_ort::observations::ConfigEntry {
    fn key(&self) -> &[u8] {
        cairn_semantic_ort::observations::ConfigEntry::key(self)
    }
    fn value(&self) -> &[u8] {
        cairn_semantic_ort::observations::ConfigEntry::value(self)
    }
}
impl MemoryObservationSource for cairn_semantic_ort::observations::MemoryObservation {
    fn name(&self) -> &[u8] {
        cairn_semantic_ort::observations::MemoryObservation::name(self)
    }
    fn device_type(&self) -> i32 {
        cairn_semantic_ort::observations::MemoryObservation::device_type(self).code()
    }
    fn device_id(&self) -> i32 {
        cairn_semantic_ort::observations::MemoryObservation::device_id(self)
    }
    fn allocator_type(&self) -> i32 {
        cairn_semantic_ort::observations::MemoryObservation::allocator_type(self).code()
    }
    fn memory_type(&self) -> i32 {
        cairn_semantic_ort::observations::MemoryObservation::memory_type(self).code()
    }
    fn device_memory_type(&self) -> i32 {
        cairn_semantic_ort::observations::MemoryObservation::device_memory_type(self).code()
    }
    fn vendor_id(&self) -> u32 {
        cairn_semantic_ort::observations::MemoryObservation::vendor_id(self)
    }
}
impl EpDeviceSource for cairn_semantic_ort::observations::EpDeviceObservation {
    fn name(&self) -> Option<&[u8]> {
        cairn_semantic_ort::observations::EpDeviceObservation::name(self)
    }
    fn vendor(&self) -> Option<&[u8]> {
        cairn_semantic_ort::observations::EpDeviceObservation::vendor(self)
    }
}
impl IoObservationSource for cairn_semantic_ort::observations::IoObservation {
    type Memory = cairn_semantic_ort::observations::MemoryObservation;
    type EpDevice = cairn_semantic_ort::observations::EpDeviceObservation;
    fn ordinal(&self) -> usize {
        cairn_semantic_ort::observations::IoObservation::ordinal(self)
    }
    fn node_name(&self) -> &str {
        cairn_semantic_ort::observations::IoObservation::node_name(self)
    }
    fn memory(&self) -> Option<&Self::Memory> {
        cairn_semantic_ort::observations::IoObservation::memory(self)
    }
    fn ep_device(&self) -> Option<&Self::EpDevice> {
        cairn_semantic_ort::observations::IoObservation::ep_device(self)
    }
}
impl AssignedNodeSource for cairn_semantic_ort::observations::AssignedNode {
    fn name(&self) -> &[u8] {
        cairn_semantic_ort::observations::AssignedNode::name(self)
    }
    fn domain(&self) -> &[u8] {
        cairn_semantic_ort::observations::AssignedNode::domain(self)
    }
    fn operator_type(&self) -> &[u8] {
        cairn_semantic_ort::observations::AssignedNode::operator_type(self)
    }
}
impl AssignedSubgraphSource for cairn_semantic_ort::observations::AssignedSubgraph {
    type Node = cairn_semantic_ort::observations::AssignedNode;
    fn provider(&self) -> &[u8] {
        cairn_semantic_ort::observations::AssignedSubgraph::provider(self)
    }
    fn nodes(&self) -> &[Self::Node] {
        cairn_semantic_ort::observations::AssignedSubgraph::nodes(self)
    }
}
impl GraphAssignmentSource for cairn_semantic_ort::observations::GraphAssignmentObservations {
    type Subgraph = cairn_semantic_ort::observations::AssignedSubgraph;
    fn subgraphs(&self) -> &[Self::Subgraph] {
        cairn_semantic_ort::observations::GraphAssignmentObservations::subgraphs(self)
    }
}
impl SessionObservationSource for cairn_semantic_ort::observations::SessionObservations {
    type Entry = cairn_semantic_ort::observations::ConfigEntry;
    type Io = cairn_semantic_ort::observations::IoObservation;
    type Assignment = cairn_semantic_ort::observations::GraphAssignmentObservations;
    fn options_config_entries(&self) -> &[Self::Entry] {
        cairn_semantic_ort::observations::SessionObservations::options_config_entries(self)
    }
    fn inputs(&self) -> &[Self::Io] {
        cairn_semantic_ort::observations::SessionObservations::inputs(self)
    }
    fn outputs(&self) -> &[Self::Io] {
        cairn_semantic_ort::observations::SessionObservations::outputs(self)
    }
    fn graph_assignment(&self) -> &Self::Assignment {
        cairn_semantic_ort::observations::SessionObservations::graph_assignment(self)
    }
}

fn session_observations_json<S: SessionObservationSource>(observations: &S) -> Value {
    let io = |rows: &[S::Io]| -> Vec<Value> {
        rows.iter().map(|row| {
            let memory = row.memory().map(|m| json!({
                "name_hex":hex(m.name()),"device_type":m.device_type(),"device_id":m.device_id(),
                "allocator_type":m.allocator_type(),"memory_type":m.memory_type(),
                "device_memory_type":m.device_memory_type(),"vendor_id":m.vendor_id()
            }));
            let ep = row.ep_device().map(|ep| json!({
                "name_hex":ep.name().map(hex),"vendor_hex":ep.vendor().map(hex)
            }));
            json!({"ordinal":row.ordinal(),"node_name":row.node_name(),"memory":memory,"ep_device":ep})
        }).collect()
    };
    json!({
        "options_config_entries": {"observed":true,"entries":observations.options_config_entries().iter().map(|e|
            json!({"key_hex":hex(e.key()),"value_hex":hex(e.value())})).collect::<Vec<_>>()},
        "options_stage":"after explicit setters, before CreateSessionFromArray",
        "session_io_stage":"fully initialized owning session, metadata ordinal order",
        "ep_graph_assignment":graph_assignment_json(observations.graph_assignment()),
        "inputs":io(observations.inputs()),"outputs":io(observations.outputs()),
        "scope":"partial I/O placement, optional EP devices and assignment-stage subgraphs; not global defaults or complete final dispatch"
    })
}
fn graph_assignment_json<G: GraphAssignmentSource>(assignment: &G) -> Value {
    json!({
        "observed":true,
        "api":"OrtApi29.Session_GetEpGraphAssignmentInfo",
        "recording_setting":{"key":"session.record_ep_graph_assignment_info","value":"1"},
        "collection_stage":"API29 documents BASIC/minimal-stage assignment; retained implementation records partitioning after Level1/basic and any InsertCastTransformer assignment callbacks during optimization loops after Level2/3; this event history does not certify the complete final graph or executable dispatch after all transformations",
        "coverage":"API-returned subgraphs/nodes only; not complete final executable provider/node dispatch",
        "subgraphs":assignment.subgraphs().iter().map(|subgraph| json!({
            "provider_hex":hex(subgraph.provider()),
            "nodes":subgraph.nodes().iter().map(|node| json!({
                "name_hex":hex(node.name()),"domain_hex":hex(node.domain()),
                "operator_type_hex":hex(node.operator_type())
            })).collect::<Vec<_>>()
        })).collect::<Vec<_>>()
    })
}
fn session_inputs(
    settings: &cairn_semantic_ort::SessionSettings,
    metadata: &cairn_semantic_ort::SessionMetadata,
    partial: Value,
) -> Value {
    let nodes = |rows: &[cairn_semantic_ort::NodeMetadata]| -> Vec<Value> {
        rows.iter().map(|n| json!({"name":n.name,"element_type":format!("{:?}",n.element_type),"dimensions":n.dimensions})).collect()
    };
    json!({"intra_op_cap":settings.intra_op_threads,"inter_op_cap":settings.inter_op_threads,
        "optimization":settings.graph_optimization,"deterministic":settings.deterministic_compute,
        "profiling":settings.profiling,"execution_mode":settings.execution_mode,
        "memory_pattern":settings.memory_pattern_enabled,"cpu_arena":null,"remaining_defaults":null,"effective_provider":null,
        "configured": {"intra_op_cap":settings.intra_op_threads,"inter_op_cap":settings.inter_op_threads,
            "optimization":settings.graph_optimization,"deterministic":settings.deterministic_compute,"profiling":settings.profiling},
        "option_getters": {"execution_mode":settings.execution_mode,"memory_pattern":settings.memory_pattern_enabled},
        "partial_observations":partial,"batch_size":1,"inputs":nodes(&metadata.inputs),"outputs":nodes(&metadata.outputs)})
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
            running_image: None,
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

#[cfg(test)]
mod observe_tests {
    use super::*;
    #[test]
    fn p2_observe1_all_four_compilation_fields_require_real_shapes() {
        let mut input = json!({"compilation":{"rustc":true,"target":true,"enabled_features":true,"effective_flags":true}});
        let fields = required_observations(&input);
        for name in [
            "compilation.rustc",
            "compilation.target",
            "compilation.enabled_features",
            "compilation.effective_flags",
        ] {
            assert!(fields.iter().any(|v| v.field == name));
        }
        input["compilation"]["rustc"] = json!("fixture version string");
        input["compilation"]["target"] = json!("x86_64-unknown-linux-gnu");
        input["compilation"]["enabled_features"] = json!({"fixture:target":["a","b"]});
        input["compilation"]["effective_flags"] = json!({"fixture:target":["first","second"]});
        let fields = required_observations(&input);
        assert!(!fields.iter().any(|v| [
            "compilation.rustc",
            "compilation.target",
            "compilation.enabled_features",
            "compilation.effective_flags"
        ]
        .contains(&v.field)));
        assert!(fields
            .iter()
            .any(|v| v.field == "compilation.linked_input_attestation"));
        input["compilation"]["enabled_features"] = json!({"fixture:target":["b","a"]});
        assert!(required_observations(&input)
            .iter()
            .any(|v| v.field == "compilation.enabled_features"));
    }
    #[test]
    fn p2_observe1_generic_ready_blobs_do_not_replace_observed_fields() {
        for pointer in [
            "/compilation/linked_input_attestation",
            "/native/ort_dependency_inventory",
            "/native/cpu_kernel_class",
            "/assets_model_external_data_closure",
        ] {
            assert!(!required_shape(pointer, Some(&json!(true)), &json!({})));
            assert!(!required_shape(
                pointer,
                Some(&json!({"ready":true})),
                &json!({})
            ));
        }
        assert!(!required_shape(
            "/compilation/linked_input_attestation",
            Some(&json!({"binary_sha256":"different","source_inputs":["x"]})),
            &json!({"compilation":{"binary_sha256":"actual"}})
        ));
    }
}

#[cfg(test)]
mod session_observation_tests {
    use super::*;
    fn settings() -> cairn_semantic_ort::SessionSettings {
        use cairn_semantic_ort::NativeDefault;
        cairn_semantic_ort::SessionSettings {
            intra_op_threads: 2,
            inter_op_threads: 2,
            graph_optimization: "ORT_ENABLE_ALL",
            deterministic_compute: false,
            profiling: false,
            execution_mode: 0,
            memory_pattern_enabled: true,
            cpu_arena: NativeDefault::NotExposedBySelectedCapi,
            remaining_defaults: NativeDefault::NotExposedBySelectedCapi,
        }
    }
    #[test]
    fn p2_session1_partial_entries_or_io_cannot_resolve_global_session_gaps() {
        let metadata = cairn_semantic_ort::SessionMetadata {
            inputs: vec![],
            outputs: vec![],
        };
        for partial in [
            Value::Null,
            json!({"options_config_entries":{"observed":true,"entries":[]}}),
            json!({"cpu_arena":true,"remaining_defaults":{"all":"ready"},"effective_provider":"CPUExecutionProvider","inputs":[{"memory":{"device_type":0,"allocator_type":1},"ep_device":null}]}),
            json!({"outputs":[{"ep_device":{"name_hex":"435055"}}]}),
        ] {
            let inputs = json!({"session":session_inputs(&settings(),&metadata,partial)});
            for field in ["cpu_arena", "remaining_defaults", "effective_provider"] {
                assert!(inputs["session"][field].is_null());
                assert!(required_observations(&inputs)
                    .iter()
                    .any(|v| v.field == format!("session.{field}")));
            }
            let report = ConstructionReport {
                missing: vec![],
                inputs,
                provenance: vec![],
                running_image: None,
            };
            assert!(report.rank_identity().is_err());
        }
    }
    #[test]
    fn p2_session1_configured_caps_getters_and_ordinal_arrays_stay_separate() {
        let metadata = cairn_semantic_ort::SessionMetadata {
            inputs: vec![],
            outputs: ["zulu", "alpha"]
                .into_iter()
                .map(|name| cairn_semantic_ort::NodeMetadata {
                    name: name.into(),
                    element_type: cairn_semantic_ort::ElementType::Float32,
                    dimensions: vec![1, 384],
                })
                .collect(),
        };
        let partial = json!({"inputs":[],"outputs":[{"ordinal":0,"memory":{"device_type":99,"name_hex":"ff"}},{"ordinal":1,"ep_device":null}],"options_config_entries":{"observed":true,"entries":[{"key_hex":"ff","value_hex":"fe"}]}});
        let inputs = session_inputs(&settings(), &metadata, partial.clone());
        assert_eq!(inputs["partial_observations"], partial);
        assert_eq!(inputs["inputs"], json!([]));
        assert_eq!(inputs["outputs"][0]["name"], "zulu");
        assert_eq!(inputs["outputs"][1]["name"], "alpha");
        assert_eq!(inputs["configured"]["intra_op_cap"], 2);
        assert_eq!(inputs["option_getters"]["execution_mode"], 0);
        assert!(inputs["configured"].get("memory_pattern").is_none());
        let mut changed = inputs.clone();
        changed["partial_observations"]["outputs"]
            .as_array_mut()
            .unwrap()
            .reverse();
        assert_ne!(canonical(&inputs).unwrap(), canonical(&changed).unwrap());
        changed = inputs.clone();
        changed["partial_observations"]["options_config_entries"]["entries"][0]["value_hex"] =
            json!("ff");
        assert_ne!(canonical(&inputs).unwrap(), canonical(&changed).unwrap());
    }

    #[test]
    fn p2_dispatch1_assignment_snapshot_cannot_fill_global_identity() {
        let metadata = cairn_semantic_ort::SessionMetadata {
            inputs: vec![],
            outputs: vec![],
        };
        let partial = json!({"ep_graph_assignment":{
            "observed":true,"api":"OrtApi29.Session_GetEpGraphAssignmentInfo",
            "recording_setting":{"key":"session.record_ep_graph_assignment_info","value":"1"},
            "subgraphs":[{"provider_hex":"435055","nodes":[{"name_hex":"61","domain_hex":"","operator_type_hex":"416464"}]}],
            "effective_provider":"CPU","cpu_arena":true,"remaining_defaults":{"complete":true},"complete_final_dispatch":true
        }});
        let inputs = json!({"session":session_inputs(&settings(), &metadata, partial)});
        for field in ["cpu_arena", "remaining_defaults", "effective_provider"] {
            assert!(inputs["session"][field].is_null());
            assert!(required_observations(&inputs)
                .iter()
                .any(|missing| missing.field == format!("session.{field}")));
        }
        assert_eq!(
            required_observations(&inputs).len(),
            required_observations(
                &json!({"session":session_inputs(&settings(), &metadata, Value::Null)})
            )
            .len()
        );
        let report = ConstructionReport {
            missing: vec![],
            inputs,
            provenance: vec![],
            running_image: None,
        };
        assert!(report.rank_identity().is_err());
    }
}

#[cfg(test)]
mod typed_serializer_tests {
    use super::*;

    type FixtureEntry = (Vec<u8>, Vec<u8>);
    type FixtureEp = (Option<Vec<u8>>, Option<Vec<u8>>);
    type FixtureNode = (Vec<u8>, Vec<u8>, Vec<u8>);
    type FixtureSubgraph = (Vec<u8>, Vec<FixtureNode>);
    struct FixtureMemory {
        name: Vec<u8>,
        device_type: i32,
        device_id: i32,
        allocator_type: i32,
        memory_type: i32,
        device_memory_type: i32,
        vendor_id: u32,
    }
    struct FixtureIo {
        ordinal: usize,
        node_name: String,
        memory: Option<FixtureMemory>,
        ep_device: Option<FixtureEp>,
    }
    #[derive(Default)]
    struct FixtureSession {
        entries: Vec<FixtureEntry>,
        inputs: Vec<FixtureIo>,
        outputs: Vec<FixtureIo>,
        assignment: Vec<FixtureSubgraph>,
    }
    impl ConfigEntrySource for FixtureEntry {
        fn key(&self) -> &[u8] {
            &self.0
        }
        fn value(&self) -> &[u8] {
            &self.1
        }
    }
    impl MemoryObservationSource for FixtureMemory {
        fn name(&self) -> &[u8] {
            &self.name
        }
        fn device_type(&self) -> i32 {
            self.device_type
        }
        fn device_id(&self) -> i32 {
            self.device_id
        }
        fn allocator_type(&self) -> i32 {
            self.allocator_type
        }
        fn memory_type(&self) -> i32 {
            self.memory_type
        }
        fn device_memory_type(&self) -> i32 {
            self.device_memory_type
        }
        fn vendor_id(&self) -> u32 {
            self.vendor_id
        }
    }
    impl EpDeviceSource for FixtureEp {
        fn name(&self) -> Option<&[u8]> {
            self.0.as_deref()
        }
        fn vendor(&self) -> Option<&[u8]> {
            self.1.as_deref()
        }
    }
    impl IoObservationSource for FixtureIo {
        type Memory = FixtureMemory;
        type EpDevice = FixtureEp;
        fn ordinal(&self) -> usize {
            self.ordinal
        }
        fn node_name(&self) -> &str {
            &self.node_name
        }
        fn memory(&self) -> Option<&Self::Memory> {
            self.memory.as_ref()
        }
        fn ep_device(&self) -> Option<&Self::EpDevice> {
            self.ep_device.as_ref()
        }
    }
    impl AssignedNodeSource for FixtureNode {
        fn name(&self) -> &[u8] {
            &self.0
        }
        fn domain(&self) -> &[u8] {
            &self.1
        }
        fn operator_type(&self) -> &[u8] {
            &self.2
        }
    }
    impl AssignedSubgraphSource for FixtureSubgraph {
        type Node = FixtureNode;
        fn provider(&self) -> &[u8] {
            &self.0
        }
        fn nodes(&self) -> &[Self::Node] {
            &self.1
        }
    }
    impl GraphAssignmentSource for Vec<FixtureSubgraph> {
        type Subgraph = FixtureSubgraph;
        fn subgraphs(&self) -> &[Self::Subgraph] {
            self
        }
    }
    impl SessionObservationSource for FixtureSession {
        type Entry = FixtureEntry;
        type Io = FixtureIo;
        type Assignment = Vec<FixtureSubgraph>;
        fn options_config_entries(&self) -> &[Self::Entry] {
            &self.entries
        }
        fn inputs(&self) -> &[Self::Io] {
            &self.inputs
        }
        fn outputs(&self) -> &[Self::Io] {
            &self.outputs
        }
        fn graph_assignment(&self) -> &Self::Assignment {
            &self.assignment
        }
    }

    fn full_fixture() -> FixtureSession {
        FixtureSession {
            entries: vec![
                (b"a".to_vec(), vec![254]),
                (b"a".to_vec(), vec![254]),
                (vec![255], b"z".to_vec()),
            ],
            inputs: vec![
                FixtureIo {
                    ordinal: 0,
                    node_name: "z-in".into(),
                    memory: Some(FixtureMemory {
                        name: vec![254, 255],
                        device_type: 37,
                        device_id: -9,
                        allocator_type: -1,
                        memory_type: -2,
                        device_memory_type: 23,
                        vendor_id: 4660,
                    }),
                    ep_device: None,
                },
                FixtureIo {
                    ordinal: 1,
                    node_name: "β-in".into(),
                    memory: None,
                    ep_device: Some((None, Some(vec![]))),
                },
                FixtureIo {
                    ordinal: 2,
                    node_name: "a-in".into(),
                    memory: Some(FixtureMemory {
                        name: vec![],
                        device_type: -13,
                        device_id: 7,
                        allocator_type: 2,
                        memory_type: -4,
                        device_memory_type: 99,
                        vendor_id: 57,
                    }),
                    ep_device: Some((Some(vec![]), None)),
                },
            ],
            outputs: vec![
                FixtureIo {
                    ordinal: 0,
                    node_name: "z-out".into(),
                    memory: None,
                    ep_device: Some((Some(vec![255]), Some(vec![254]))),
                },
                FixtureIo {
                    ordinal: 1,
                    node_name: "a-out".into(),
                    memory: None,
                    ep_device: None,
                },
                FixtureIo {
                    ordinal: 2,
                    node_name: "null-out".into(),
                    memory: None,
                    ep_device: Some((None, None)),
                },
            ],
            assignment: vec![
                (
                    vec![0x00, 0x0a, 0xff],
                    vec![
                        (b"z".to_vec(), vec![], b"Add".to_vec()),
                        (b"z".to_vec(), vec![], b"Add".to_vec()),
                    ],
                ),
                (
                    b"CPU".to_vec(),
                    vec![(vec![254], b"ai.onnx".to_vec(), b"Mul".to_vec())],
                ),
                (vec![], vec![]),
            ],
        }
    }

    #[test]
    fn p2_t1_source_typed_fixture_matches_independent_full_contract() {
        let actual = session_observations_json(&full_fixture());
        let expected = json!({
            "options_config_entries":{"observed":true,"entries":[
                {"key_hex":"61","value_hex":"fe"}, {"key_hex":"61","value_hex":"fe"},
                {"key_hex":"ff","value_hex":"7a"}
            ]},
            "options_stage":"after explicit setters, before CreateSessionFromArray",
            "session_io_stage":"fully initialized owning session, metadata ordinal order",
            "inputs":[
                {"ordinal":0,"node_name":"z-in","memory":{"name_hex":"feff","device_type":37,"device_id":-9,"allocator_type":-1,"memory_type":-2,"device_memory_type":23,"vendor_id":4660},"ep_device":null},
                {"ordinal":1,"node_name":"β-in","memory":null,"ep_device":{"name_hex":null,"vendor_hex":""}},
                {"ordinal":2,"node_name":"a-in","memory":{"name_hex":"","device_type":-13,"device_id":7,"allocator_type":2,"memory_type":-4,"device_memory_type":99,"vendor_id":57},"ep_device":{"name_hex":"","vendor_hex":null}}
            ],
            "outputs":[
                {"ordinal":0,"node_name":"z-out","memory":null,"ep_device":{"name_hex":"ff","vendor_hex":"fe"}},
                {"ordinal":1,"node_name":"a-out","memory":null,"ep_device":null},
                {"ordinal":2,"node_name":"null-out","memory":null,"ep_device":{"name_hex":null,"vendor_hex":null}}
            ],
            "ep_graph_assignment":{
                "observed":true,
                "api":"OrtApi29.Session_GetEpGraphAssignmentInfo",
                "recording_setting":{"key":"session.record_ep_graph_assignment_info","value":"1"},
                "collection_stage":"API29 documents BASIC/minimal-stage assignment; retained implementation records partitioning after Level1/basic and any InsertCastTransformer assignment callbacks during optimization loops after Level2/3; this event history does not certify the complete final graph or executable dispatch after all transformations",
                "coverage":"API-returned subgraphs/nodes only; not complete final executable provider/node dispatch",
                "subgraphs":[
                    {"provider_hex":"000aff","nodes":[{"name_hex":"7a","domain_hex":"","operator_type_hex":"416464"},{"name_hex":"7a","domain_hex":"","operator_type_hex":"416464"}]},
                    {"provider_hex":"435055","nodes":[{"name_hex":"fe","domain_hex":"61692e6f6e6e78","operator_type_hex":"4d756c"}]},
                    {"provider_hex":"","nodes":[]}
                ]
            },
            "scope":"partial I/O placement, optional EP devices and assignment-stage subgraphs; not global defaults or complete final dispatch"
        });
        assert_eq!(actual, expected);
    }

    #[test]
    fn p2_t1_source_empty_collections_stay_observed_and_present() {
        let actual = session_observations_json(&FixtureSession::default());
        assert_eq!(
            actual.get("options_config_entries"),
            Some(&json!({"observed":true,"entries":[]}))
        );
        assert_eq!(actual.get("inputs"), Some(&json!([])));
        assert_eq!(actual.get("outputs"), Some(&json!([])));
        assert_eq!(
            actual["ep_graph_assignment"].get("observed"),
            Some(&json!(true))
        );
        assert_eq!(
            actual["ep_graph_assignment"].get("subgraphs"),
            Some(&json!([]))
        );
    }

    #[test]
    fn p2_t1_source_order_grouping_and_duplicates_are_not_canonicalized_away() {
        let mut fixture = FixtureSession::default();
        fixture.assignment = vec![
            (
                b"C".to_vec(),
                vec![
                    (b"z".to_vec(), vec![], b"Add".to_vec()),
                    (b"a".to_vec(), vec![], b"Add".to_vec()),
                    (b"z".to_vec(), vec![], b"Add".to_vec()),
                ],
            ),
            (
                b"C".to_vec(),
                vec![(b"b".to_vec(), vec![], b"Mul".to_vec())],
            ),
        ];
        let actual = session_observations_json(&fixture);
        assert_eq!(
            actual["ep_graph_assignment"]["subgraphs"],
            json!([
                {"provider_hex":"43","nodes":[{"name_hex":"7a","domain_hex":"","operator_type_hex":"416464"},{"name_hex":"61","domain_hex":"","operator_type_hex":"416464"},{"name_hex":"7a","domain_hex":"","operator_type_hex":"416464"}]},
                {"provider_hex":"43","nodes":[{"name_hex":"62","domain_hex":"","operator_type_hex":"4d756c"}]}
            ])
        );
        fixture.assignment.reverse();
        let actual = session_observations_json(&fixture);
        assert_eq!(
            actual["ep_graph_assignment"]["subgraphs"],
            json!([
                {"provider_hex":"43","nodes":[{"name_hex":"62","domain_hex":"","operator_type_hex":"4d756c"}]},
                {"provider_hex":"43","nodes":[{"name_hex":"7a","domain_hex":"","operator_type_hex":"416464"},{"name_hex":"61","domain_hex":"","operator_type_hex":"416464"},{"name_hex":"7a","domain_hex":"","operator_type_hex":"416464"}]}
            ])
        );
        fixture.assignment.reverse();
        let tail = fixture.assignment.pop().unwrap().1;
        fixture.assignment[0].1.extend(tail);
        let actual = session_observations_json(&fixture);
        assert_eq!(
            actual["ep_graph_assignment"]["subgraphs"],
            json!([
                {"provider_hex":"43","nodes":[{"name_hex":"7a","domain_hex":"","operator_type_hex":"416464"},{"name_hex":"61","domain_hex":"","operator_type_hex":"416464"},{"name_hex":"7a","domain_hex":"","operator_type_hex":"416464"},{"name_hex":"62","domain_hex":"","operator_type_hex":"4d756c"}]}
            ])
        );
    }

    #[test]
    fn p2_t1_source_shared_serializer_preserves_global_readiness_refusal() {
        use cairn_semantic_ort::{NativeDefault, SessionMetadata, SessionSettings};
        let settings = SessionSettings {
            intra_op_threads: 2,
            inter_op_threads: 2,
            graph_optimization: "ORT_ENABLE_ALL",
            deterministic_compute: false,
            profiling: false,
            execution_mode: 0,
            memory_pattern_enabled: true,
            cpu_arena: NativeDefault::NotExposedBySelectedCapi,
            remaining_defaults: NativeDefault::NotExposedBySelectedCapi,
        };
        let fixture = full_fixture();
        let nodes = |rows: &[FixtureIo]| {
            rows.iter()
                .map(|row| cairn_semantic_ort::NodeMetadata {
                    name: row.node_name.clone(),
                    element_type: cairn_semantic_ort::ElementType::Float32,
                    dimensions: vec![1, 384],
                })
                .collect::<Vec<_>>()
        };
        let metadata = SessionMetadata {
            inputs: nodes(&fixture.inputs),
            outputs: nodes(&fixture.outputs),
        };
        let partial = session_observations_json(&fixture);
        let inputs = json!({"session":session_inputs(&settings,&metadata,partial)});
        for field in ["cpu_arena", "remaining_defaults", "effective_provider"] {
            assert_eq!(inputs["session"].get(field), Some(&Value::Null));
            assert!(required_observations(&inputs)
                .iter()
                .any(|gap| gap.field == format!("session.{field}")));
        }
        let report = ConstructionReport {
            inputs,
            provenance: vec![],
            missing: vec![],
            running_image: None,
        };
        assert!(report.rank_identity().is_err());
    }
}
