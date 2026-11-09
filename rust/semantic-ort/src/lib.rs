//! Owning API29 inference component. Output is raw CLS bits, before BOTH normalizations.
//! This crate implements neither the rank BackendSession nor a worker identity/handshake.
mod ffi;
pub mod metadata;

use cairn_semantic_tokenizer::{BatchOneTensors, ConsumedFile, PreparedTokenizer};
pub use metadata::{ElementType, NodeMetadata, RawClsBits, SessionMetadata};
use sha2::{Digest, Sha256};
use std::cell::RefCell;
use std::ffi::CStr;
use std::fs::File;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::ptr::NonNull;
use std::rc::Rc;

pub const RUNTIME_BYTES: usize = 28_501_848;
pub const RUNTIME_SHA256: &str = "8902e52a04bf6bf26f1613746811d6194f9c97ce7a6a7a28452b48522d3bd8d0";

#[derive(Debug)]
pub enum Error {
    Io {
        stage: &'static str,
        path: PathBuf,
        source: std::io::Error,
    },
    Contract(String),
    Native {
        stage: String,
        code: i32,
        detail: Vec<u8>,
        error_copy_failed: bool,
    },
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io { source, .. } => Some(source),
            _ => None,
        }
    }
}
fn contract(message: impl Into<String>) -> Error {
    Error::Contract(message.into())
}
fn sha256(bytes: &[u8]) -> [u8; 32] {
    Sha256::digest(bytes).into()
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RuntimeFile {
    /// Location is provenance, not a derivation-identity input.
    pub path: PathBuf,
    pub device: u64,
    pub inode: u64,
    pub bytes: usize,
    pub sha256: [u8; 32],
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RuntimeProvenance {
    pub selected_file: RuntimeFile,
    /// Re-hashed sealed file backing the actual mapped OrtGetApiBase address.
    pub mapped_file: RuntimeFile,
    pub api_version: u32,
    pub reported_version: &'static str,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum QualificationObligation {
    LauncherEnvironmentBeforeProcessInitialization,
    FreshProcessOrtEnvironment,
    SealedMemfdAndProcMapsOnReleaseHost,
    DynamicDependenciesCpuAbiAndDispatch,
    NativeDefaultsAgainstFrozenPython,
    SelfContainedModelAndAllConsumedAssets,
    RawOutputBitsAgainstReferenceAfterAll294TokenizerInputs,
    NoWritesOrConnectsAcrossWorkerProcessTree,
    CompleteDerivationManifestAndP3Arithmetic,
}
pub const QUALIFICATION_OBLIGATIONS: &[QualificationObligation] = &[
    QualificationObligation::LauncherEnvironmentBeforeProcessInitialization,
    QualificationObligation::FreshProcessOrtEnvironment,
    QualificationObligation::SealedMemfdAndProcMapsOnReleaseHost,
    QualificationObligation::DynamicDependenciesCpuAbiAndDispatch,
    QualificationObligation::NativeDefaultsAgainstFrozenPython,
    QualificationObligation::SelfContainedModelAndAllConsumedAssets,
    QualificationObligation::RawOutputBitsAgainstReferenceAfterAll294TokenizerInputs,
    QualificationObligation::NoWritesOrConnectsAcrossWorkerProcessTree,
    QualificationObligation::CompleteDerivationManifestAndP3Arithmetic,
];

/// Drop cannot return Result. Keep this observer after dropping all sessions and
/// runtimes, then drain it so native dlclose/close failures remain visible.
#[derive(Clone, Default, Debug)]
pub struct CleanupErrors(Rc<RefCell<Vec<Error>>>);
impl CleanupErrors {
    pub fn drain(&self) -> Vec<Error> {
        self.0.borrow_mut().drain(..).collect()
    }
}
struct RuntimeHandle {
    pointer: NonNull<ffi::Runtime>,
    provenance: Option<RuntimeProvenance>,
    cleanup: CleanupErrors,
}
impl Drop for RuntimeHandle {
    fn drop(&mut self) {
        let mut error = ffi::NativeError::new();
        let code = unsafe { ffi::cairn_ort_runtime_close(self.pointer.as_ptr(), &mut error) };
        if let Err(problem) = error.result(code) {
            self.cleanup.0.borrow_mut().push(problem);
        }
    }
}

fn runtime_file(path: &Path) -> Result<(Vec<u8>, RuntimeFile), Error> {
    let file = File::open(path).map_err(|source| Error::Io {
        stage: "open runtime",
        path: path.into(),
        source,
    })?;
    let metadata = file.metadata().map_err(|source| Error::Io {
        stage: "stat runtime",
        path: path.into(),
        source,
    })?;
    if !metadata.is_file() || metadata.len() != RUNTIME_BYTES as u64 {
        return Err(contract("selected runtime has wrong file type/length"));
    }
    let mut bytes = Vec::with_capacity(RUNTIME_BYTES + 1);
    file.take((RUNTIME_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|source| Error::Io {
            stage: "read runtime",
            path: path.into(),
            source,
        })?;
    let digest = sha256(&bytes);
    if bytes.len() != RUNTIME_BYTES || hex(&digest) != RUNTIME_SHA256 {
        return Err(contract(
            "selected runtime bytes do not match the pinned 1.29.0 C-API artifact",
        ));
    }
    let record = RuntimeFile {
        path: path.into(),
        device: metadata.dev(),
        inode: metadata.ino(),
        bytes: bytes.len(),
        sha256: digest,
    };
    Ok((bytes, record))
}

/// Rc makes this owner and its sessions !Send/!Sync. A session's owner outlives
/// every OrtValue/OrtSession; the library and sealed fd close only after the last session.
pub struct Runtime(Rc<RuntimeHandle>);
impl Runtime {
    /// Actually loads native code. Call only inside the separately admitted host boundary.
    /// The caller supplies a path, never an asserted digest/version or backend identity.
    pub fn load(path: &Path, cleanup: CleanupErrors) -> Result<Self, Error> {
        if !path.is_absolute() {
            return Err(contract("runtime path must be explicitly absolute"));
        }
        let (bytes, selected_file) = runtime_file(path)?;
        let mut mapping = ffi::Mapping::default();
        let mut error = ffi::NativeError::new();
        let mut pointer = std::ptr::null_mut();
        let code = unsafe {
            ffi::cairn_ort_runtime_open(
                bytes.as_ptr(),
                bytes.len(),
                &mut pointer,
                &mut mapping,
                &mut error,
            )
        };
        error.result(code)?;
        let pointer =
            NonNull::new(pointer).ok_or_else(|| contract("runtime bridge returned no owner"))?;
        let mut handle = RuntimeHandle {
            pointer,
            provenance: None,
            cleanup,
        };
        let mapped_path = PathBuf::from(format!("/proc/self/fd/{}", mapping.fd));
        let (_, mapped_file) = runtime_file(&mapped_path)?;
        if mapping.fd < 0
            || mapping.bytes != RUNTIME_BYTES as u64
            || mapping.device != mapped_file.device
            || mapping.inode != mapped_file.inode
        {
            return Err(contract(
                "actual sealed runtime mapping does not match the re-hashed descriptor",
            ));
        }
        handle.provenance = Some(RuntimeProvenance {
            selected_file,
            mapped_file,
            api_version: 29,
            reported_version: "1.29.0",
        });
        Ok(Self(Rc::new(handle)))
    }
    pub fn provenance(&self) -> &RuntimeProvenance {
        self.0
            .provenance
            .as_ref()
            .expect("published only after load validation")
    }

    /// Creates a real ORT session from these owned, retained model bytes. The
    /// prepared tokenizer contributes its actually consumed config bytes, not a supplied manifest.
    pub fn session(&self, model: Vec<u8>, tokenizer: &PreparedTokenizer) -> Result<Session, Error> {
        if model.is_empty() {
            return Err(contract("empty model bytes"));
        }
        let model = model.into_boxed_slice();
        let consumed_model = ModelProvenance {
            name: "model_optimized.onnx",
            sha256: sha256(&model),
            bytes: model.len(),
        };
        let consumed_tokenizer = tokenizer.consumed_files().to_vec();
        for file in &consumed_tokenizer {
            if sha256(&file.bytes) != file.sha256 {
                return Err(contract("prepared tokenizer input digest mismatch"));
            }
        }
        let mut pointer = std::ptr::null_mut();
        let mut options = ffi::Options::default();
        let mut error = ffi::NativeError::new();
        let code = unsafe {
            ffi::cairn_ort_session_open(
                self.0.pointer.as_ptr(),
                model.as_ptr(),
                model.len(),
                &mut pointer,
                &mut options,
                &mut error,
            )
        };
        error.result(code)?;
        let pointer =
            NonNull::new(pointer).ok_or_else(|| contract("session bridge returned no owner"))?;
        let handle = SessionHandle {
            pointer,
            runtime: Rc::clone(&self.0),
        };
        let metadata = read_metadata(pointer)?;
        metadata.validate()?;
        if !matches!(options.execution_mode, 0 | 1) {
            return Err(contract("unknown observed session execution mode"));
        }
        let settings = SessionSettings {
            intra_op_threads: 2,
            inter_op_threads: 2,
            graph_optimization: "ORT_ENABLE_ALL",
            deterministic_compute: false,
            profiling: false,
            execution_mode: options.execution_mode,
            memory_pattern_enabled: match options.memory_pattern {
                0 => false,
                1 => true,
                _ => return Err(contract("invalid observed memory-pattern flag")),
            },
            cpu_arena: NativeDefault::NotExposedBySelectedCapi,
            remaining_defaults: NativeDefault::NotExposedBySelectedCapi,
        };
        Ok(Session {
            handle,
            model,
            consumed_model,
            consumed_tokenizer,
            metadata,
            settings,
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModelProvenance {
    pub name: &'static str,
    pub bytes: usize,
    pub sha256: [u8; 32],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NativeDefault {
    NotExposedBySelectedCapi,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionSettings {
    /// Configured ORT cap, not a whole-process thread count; see API29 OpenMP caveat.
    pub intra_op_threads: i32,
    /// Configured2; API29 documents that sequential execution ignores this setting.
    pub inter_op_threads: i32,
    pub graph_optimization: &'static str,
    pub deterministic_compute: bool,
    pub profiling: bool,
    /// Actual API29 GetSessionExecutionMode value (0 sequential,1 parallel).
    pub execution_mode: i32,
    /// Actual API29 GetMemPatternEnabled value, not an assumed default.
    pub memory_pattern_enabled: bool,
    pub cpu_arena: NativeDefault,
    pub remaining_defaults: NativeDefault,
}
struct SessionHandle {
    pointer: NonNull<ffi::Session>,
    runtime: Rc<RuntimeHandle>,
}
impl Drop for SessionHandle {
    fn drop(&mut self) {
        unsafe { ffi::cairn_ort_session_close(self.pointer.as_ptr()) }
    }
}

pub struct Session {
    // Drop the native session BEFORE its model buffer, then release the library owner.
    handle: SessionHandle,
    model: Box<[u8]>,
    consumed_model: ModelProvenance,
    consumed_tokenizer: Vec<ConsumedFile>,
    metadata: SessionMetadata,
    settings: SessionSettings,
}
impl Session {
    pub fn metadata(&self) -> &SessionMetadata {
        &self.metadata
    }
    pub fn settings(&self) -> &SessionSettings {
        &self.settings
    }
    pub fn runtime_provenance(&self) -> &RuntimeProvenance {
        self.handle
            .runtime
            .provenance
            .as_ref()
            .expect("validated runtime")
    }
    pub fn model_bytes(&self) -> &[u8] {
        &self.model
    }
    pub fn model_provenance(&self) -> &ModelProvenance {
        &self.consumed_model
    }
    pub fn consumed_tokenizer_files(&self) -> &[ConsumedFile] {
        &self.consumed_tokenizer
    }

    /// Own backing int64 arrays until synchronous Run returns; never cross-pad texts.
    pub fn raw_cls(&mut self, mut batch: BatchOneTensors) -> Result<RawClsBits, Error> {
        let sequence = self.metadata.validate_batch(&batch)?;
        let mut output = ffi::Output::new();
        let mut error = ffi::NativeError::new();
        let code = unsafe {
            ffi::cairn_ort_run(
                self.handle.pointer.as_ptr(),
                batch.input_ids.as_mut_ptr(),
                batch.attention_mask.as_mut_ptr(),
                batch.token_type_ids.as_mut_ptr(),
                sequence,
                &mut output,
                &mut error,
            )
        };
        error.result(code)?;
        let expected = sequence
            .checked_mul(metadata::DIMENSIONS)
            .ok_or_else(|| contract("output count overflow"))?;
        if output.bits.is_null() || output.count != expected {
            return Err(contract("invalid bridge output storage/count"));
        }
        // The bridge owns exactly count u32 objects until Output::drop; shape/count
        // validation bounds the slice before constructing it. No floating arithmetic.
        let bits = unsafe { std::slice::from_raw_parts(output.bits, output.count) };
        metadata::extract_cls(&output.dimensions, bits, sequence)
    }
    /// Query first and passage order are the iterator order. No deduplication,
    /// batch coalescing, normalization, or reordering is performed here.
    pub fn raw_cls_in_order(
        &mut self,
        batches: impl IntoIterator<Item = BatchOneTensors>,
    ) -> Result<Vec<RawClsBits>, Error> {
        batches
            .into_iter()
            .map(|batch| self.raw_cls(batch))
            .collect()
    }
}

fn read_metadata(pointer: NonNull<ffi::Session>) -> Result<SessionMetadata, Error> {
    let read = |input: i32| -> Result<Vec<NodeMetadata>, Error> {
        let count = unsafe { ffi::cairn_ort_node_count(pointer.as_ptr(), input) };
        if count > 64 {
            return Err(contract("native metadata count out of bounds"));
        }
        let mut nodes = Vec::with_capacity(count);
        for index in 0..count {
            let mut node = ffi::Node::new();
            let mut error = ffi::NativeError::new();
            let code = unsafe {
                ffi::cairn_ort_node(pointer.as_ptr(), input, index, &mut node, &mut error)
            };
            error.result(code)?;
            if node.name.is_null()
                || node.rank > 16
                || (node.rank != 0 && node.dimensions.is_null())
            {
                return Err(contract("invalid native metadata pointer/rank"));
            }
            // Pointers borrow this still-live owning Session. Copy every string/dimension.
            let name = unsafe { CStr::from_ptr(node.name) }
                .to_str()
                .map_err(|_| contract("session node name is not UTF-8"))?
                .to_owned();
            let dimensions = if node.rank == 0 {
                Vec::new()
            } else {
                unsafe { std::slice::from_raw_parts(node.dimensions, node.rank) }.to_vec()
            };
            let element_type = match node.kind {
                1 => ElementType::Float32,
                2 => ElementType::Int64,
                0 => ElementType::Other(node.ort_type),
                _ => return Err(contract("unknown bridge tensor kind")),
            };
            nodes.push(NodeMetadata {
                name,
                element_type,
                dimensions,
            });
        }
        Ok(nodes)
    };
    Ok(SessionMetadata {
        inputs: read(1)?,
        outputs: read(0)?,
    })
}

#[cfg(test)]
mod tests;
