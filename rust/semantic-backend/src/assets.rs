//! Explicit consumed assets; each caller owns its construction-order policy.
use crate::{arithmetic::operation, identity, LoadedBackend};
use cairn_semantic_rank::backend::BackendError;
use cairn_semantic_tokenizer::{PreparedInputs, PreparedTokenizer};
use std::{
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    rc::Rc,
};

#[derive(Clone, Debug)]
pub struct AssetPaths {
    pub model: PathBuf,
    pub config: PathBuf,
    pub tokenizer_config: PathBuf,
    pub special_tokens_map: PathBuf,
    pub tokenizer: PathBuf,
    pub ort_runtime: PathBuf,
    pub math: cairn_semantic_math::LibraryPaths,
    /// Repository root for a complete local-source observation; it is NOT a
    /// claim that those files produced this binary (see unresolved build binding).
    pub source_root: PathBuf,
}
#[derive(Clone, Default, Debug)]
pub struct CleanupErrors {
    pub ort: cairn_semantic_ort::CleanupErrors,
    pub math: cairn_semantic_math::CleanupErrors,
}
pub struct NativeFactory {
    paths: AssetPaths,
    cleanup: CleanupErrors,
}
impl NativeFactory {
    pub fn new(paths: AssetPaths, cleanup: CleanupErrors) -> Self {
        Self { paths, cleanup }
    }
    pub fn cleanup_errors(&self) -> &CleanupErrors {
        &self.cleanup
    }
    /// Performs actual local reads, native loading and real session creation.
    /// Execution must be admitted separately; merely building this crate does none.
    /// Retain a clone of CleanupErrors after dropping factory/session, then drain it.
    pub fn load(&self) -> Result<LoadedBackend, BackendError> {
        let config = read_owned(&self.paths.config)?;
        let tokenizer_config = read_owned(&self.paths.tokenizer_config)?;
        let special_tokens_map = read_owned(&self.paths.special_tokens_map)?;
        let tokenizer_bytes = read_owned(&self.paths.tokenizer)?;
        let tokenizer = PreparedTokenizer::from_bytes(PreparedInputs {
            config: &config,
            tokenizer_config: &tokenizer_config,
            special_tokens_map: &special_tokens_map,
            tokenizer: &tokenizer_bytes,
        })
        .map_err(|e| operation("prepare consumed tokenizer", e))?;
        let model = read_owned(&self.paths.model)?;
        // Certify the exact consumed bytes BEFORE allowing ORT to resolve any
        // external tensor filename through an ambient directory.
        let closure = crate::model_assets::observe(&model)
            .map_err(|pending| operation("unresolved consumed model asset closure", pending))?;
        let runtime =
            cairn_semantic_ort::Runtime::load(&self.paths.ort_runtime, self.cleanup.ort.clone())
                .map_err(|e| operation("load selected ORT", e))?;
        let ort = runtime
            .session(model, &tokenizer)
            .map_err(|e| operation("construct actual ORT session", e))?;
        let math = cairn_semantic_math::Runtime::load(&self.paths.math, self.cleanup.math.clone())
            .map_err(|e| operation("load selected math", e))?;
        let report = identity::observe(&self.paths, &tokenizer, &ort, &math, &closure)?;
        Ok(LoadedBackend {
            ort,
            math,
            tokenizer,
            owner: Rc::new(()),
            report,
        })
    }
}
pub(crate) fn read_owned(path: &Path) -> Result<Vec<u8>, BackendError> {
    if !path.is_absolute() {
        return Err(operation(
            "asset path",
            "explicit absolute local path required",
        ));
    }
    let mut file = File::open(path).map_err(|e| operation("open consumed asset", (path, e)))?;
    let before = file
        .metadata()
        .map_err(|e| operation("stat consumed asset", (path, e)))?;
    if !before.is_file() || before.len() > isize::MAX as u64 {
        return Err(operation(
            "asset storage",
            (path, "not a bounded regular file"),
        ));
    }
    let mut bytes = Vec::new();
    // The exact owned bytes, not a pre-read digest, feed all component constructors.
    file.by_ref()
        .take(before.len().saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(|e| operation("read consumed asset", (path, e)))?;
    let after = file
        .metadata()
        .map_err(|e| operation("restat consumed asset", (path, e)))?;
    use std::os::unix::fs::MetadataExt;
    if before.len() != after.len()
        || bytes.len() as u64 != after.len()
        || before.mtime() != after.mtime()
        || before.mtime_nsec() != after.mtime_nsec()
        || before.ctime() != after.ctime()
        || before.ctime_nsec() != after.ctime_nsec()
    {
        return Err(operation("consumed asset changed", path));
    }
    Ok(bytes)
}
