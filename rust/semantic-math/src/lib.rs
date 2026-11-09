//! Pinned native matrix-vector stage for an already-normalized full-occurrence matrix.
//! Both normalizations, full BackendSession, worker identity and qualification are separate.
mod ffi;
pub mod layout;
pub use layout::{FullOccurrenceMatrix, Layout, Operation, WIDTH};
use sha2::{Digest, Sha256};
use std::cell::RefCell;
use std::ffi::{CStr, OsStr};
use std::fs::File;
use std::io::Read;
use std::os::unix::{ffi::OsStrExt, fs::MetadataExt};
use std::path::{Path, PathBuf};
use std::ptr::NonNull;
use std::rc::Rc;

#[derive(Debug)]
pub enum Error {
    Shape(String),
    Binding(String),
    Io {
        stage: &'static str,
        path: PathBuf,
        source: std::io::Error,
    },
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
fn binding(message: impl Into<String>) -> Error {
    Error::Binding(message.into())
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Library {
    OpenBlas,
    Gfortran,
    Quadmath,
}
#[derive(Clone, Copy, Debug)]
pub struct LibraryPin {
    pub library: Library,
    pub bytes: usize,
    pub sha256: &'static str,
    pub soname: &'static str,
}
pub const LIBRARIES: [LibraryPin; 3] = [
    LibraryPin {
        library: Library::OpenBlas,
        bytes: 25409073,
        sha256: "05c9f9eb89ee68a4b9d673184fa91c99587e736392c0c2d49180a8aa5303d080",
        soname: "libscipy_openblas64_-32a4b2a6.so",
    },
    LibraryPin {
        library: Library::Gfortran,
        bytes: 2833617,
        sha256: "c6090048eccc763522c12ef016f81da6b627cb3a044f55cf0479a839c41c0980",
        soname: "libgfortran-040039e1-0352e75f.so.5.0.0",
    },
    LibraryPin {
        library: Library::Quadmath,
        bytes: 250985,
        sha256: "6ed5137f412781ad7863439fb543613f620b43c32b63292a0029246162f5bbc6",
        soname: "libquadmath-96973f99-934c22de.so.0.0.0",
    },
];
#[derive(Clone, Debug)]
pub struct LibraryPaths {
    pub openblas: PathBuf,
    pub gfortran: PathBuf,
    pub quadmath: PathBuf,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FileObservation {
    pub path: PathBuf,
    pub bytes: usize,
    pub sha256: [u8; 32],
    pub device: u64,
    pub inode: u64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ModuleRole {
    Pinned(Library),
    StandardDependency,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ModuleObservation {
    pub role: ModuleRole,
    pub file: FileObservation,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MathSettings {
    /// Actual getter bytes, not the build-time __config__.py declaration.
    pub config: Vec<u8>,
    pub core: Vec<u8>,
    pub threads: i32,
    pub parallel: i32,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Provenance {
    pub selected_files: Vec<FileObservation>,
    pub mapped_modules: Vec<ModuleObservation>,
    pub settings: MathSettings,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum QualificationObligation {
    PrivateGlibcNamespaceAndSealedMemfdOnReleaseHost,
    LaunchEnvironmentBeforeDynamicLoaderInitialization,
    ImmutableStandardDependencyMappingsAndHashes,
    ActualCpuIsaOsStateKernelThreadsAndFloatingEnvironment,
    NumPyOneChunkDotNonfiniteAndFloatingEnvironmentParity,
    BothNormalizationsAndCompleteDerivationManifest,
    BitIdenticalReferenceAndStrictIntegerComparison,
    ProcessTreeResourcesEgressWritesAndGoSupervision,
}
pub const QUALIFICATION_OBLIGATIONS: &[QualificationObligation] = &[
    QualificationObligation::PrivateGlibcNamespaceAndSealedMemfdOnReleaseHost,
    QualificationObligation::LaunchEnvironmentBeforeDynamicLoaderInitialization,
    QualificationObligation::ImmutableStandardDependencyMappingsAndHashes,
    QualificationObligation::ActualCpuIsaOsStateKernelThreadsAndFloatingEnvironment,
    QualificationObligation::NumPyOneChunkDotNonfiniteAndFloatingEnvironmentParity,
    QualificationObligation::BothNormalizationsAndCompleteDerivationManifest,
    QualificationObligation::BitIdenticalReferenceAndStrictIntegerComparison,
    QualificationObligation::ProcessTreeResourcesEgressWritesAndGoSupervision,
];

/// Retain this observer beyond Runtime drop and drain it; Drop cannot return Result.
#[derive(Clone, Default, Debug)]
pub struct CleanupErrors(Rc<RefCell<Vec<Error>>>);
impl CleanupErrors {
    pub fn drain(&self) -> Vec<Error> {
        self.0.borrow_mut().drain(..).collect()
    }
}
struct Handle {
    pointer: NonNull<ffi::Runtime>,
    cleanup: CleanupErrors,
}
impl Drop for Handle {
    fn drop(&mut self) {
        let mut error = ffi::NativeError::new();
        let code = unsafe { ffi::cairn_math_close(self.pointer.as_ptr(), &mut error) };
        if let Err(problem) = error.result(code) {
            self.cleanup.0.borrow_mut().push(problem);
        }
    }
}

const MAX_OBSERVED_LIBRARY_BYTES: usize = 128 * 1024 * 1024;
fn read_file(path: &Path, pin: Option<LibraryPin>) -> Result<(Vec<u8>, FileObservation), Error> {
    if !path.is_absolute() {
        return Err(binding("library path must be explicitly absolute"));
    }
    let file = File::open(path).map_err(|source| Error::Io {
        stage: "open math library",
        path: path.into(),
        source,
    })?;
    let before = file.metadata().map_err(|source| Error::Io {
        stage: "stat math library",
        path: path.into(),
        source,
    })?;
    if !before.is_file() || before.len() > MAX_OBSERVED_LIBRARY_BYTES as u64 {
        return Err(binding("unsupported native file type/size"));
    }
    if let Some(pin) = pin {
        if before.len() != pin.bytes as u64 {
            return Err(binding(format!(
                "selected {:?} length mismatch",
                pin.library
            )));
        }
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    let mut reader = file.take((MAX_OBSERVED_LIBRARY_BYTES + 1) as u64);
    reader.read_to_end(&mut bytes).map_err(|source| Error::Io {
        stage: "read math library",
        path: path.into(),
        source,
    })?;
    let after = reader.get_ref().metadata().map_err(|source| Error::Io {
        stage: "restat math library",
        path: path.into(),
        source,
    })?;
    if bytes.len() > MAX_OBSERVED_LIBRARY_BYTES
        || before.len() != after.len()
        || before.mtime() != after.mtime()
        || before.mtime_nsec() != after.mtime_nsec()
        || before.ctime() != after.ctime()
        || before.ctime_nsec() != after.ctime_nsec()
        || bytes.len() != after.len() as usize
    {
        return Err(binding("native file metadata/length changed during read"));
    }
    let sha256 = payload_digest(&bytes, pin)?;
    let observation = FileObservation {
        path: path.into(),
        bytes: bytes.len(),
        sha256,
        device: after.dev(),
        inode: after.ino(),
    };
    Ok((bytes, observation))
}
fn payload_digest(bytes: &[u8], pin: Option<LibraryPin>) -> Result<[u8; 32], Error> {
    let digest: [u8; 32] = Sha256::digest(bytes).into();
    if let Some(pin) = pin {
        if bytes.len() != pin.bytes || hex(&digest) != pin.sha256 {
            return Err(binding(format!(
                "selected {:?} bytes do not match exact NumPy artifact",
                pin.library
            )));
        }
    }
    Ok(digest)
}
fn copied_bytes(pointer: *const std::ffi::c_char) -> Result<Vec<u8>, Error> {
    if pointer.is_null() {
        return Err(binding("null native settings string"));
    }
    Ok(unsafe { CStr::from_ptr(pointer) }.to_bytes().to_vec())
}

/// Owns real pinned BLAS/Fortran/quadmath handles and a private native namespace.
/// CleanupErrors' Rc plus exclusive compute access makes this !Send/!Sync.
pub struct Runtime {
    handle: Handle,
    provenance: Provenance,
}
impl Runtime {
    /// Performs actual native loading/getters. Execution requires separate admission.
    pub fn load(paths: &LibraryPaths, cleanup: CleanupErrors) -> Result<Self, Error> {
        let paths = [&paths.openblas, &paths.gfortran, &paths.quadmath];
        let mut payloads = Vec::with_capacity(3);
        let mut selected_files = Vec::with_capacity(3);
        for (path, pin) in paths.into_iter().zip(LIBRARIES) {
            let (bytes, observation) = read_file(path, Some(pin))?;
            payloads.push(bytes);
            selected_files.push(observation);
        }
        let inputs: [ffi::Bytes; 3] = std::array::from_fn(|i| ffi::Bytes {
            bytes: payloads[i].as_ptr(),
            length: payloads[i].len(),
        });
        let mut pointer = std::ptr::null_mut();
        let mut settings = ffi::Settings::new();
        let mut error = ffi::NativeError::new();
        let code = unsafe {
            ffi::cairn_math_open(inputs.as_ptr(), &mut pointer, &mut settings, &mut error)
        };
        error.result(code)?;
        let pointer =
            NonNull::new(pointer).ok_or_else(|| binding("native math bridge returned no owner"))?;
        let handle = Handle { pointer, cleanup };
        let settings = MathSettings {
            config: copied_bytes(settings.config)?,
            core: copied_bytes(settings.core)?,
            threads: settings.threads,
            parallel: settings.parallel,
        };
        let count = unsafe { ffi::cairn_math_module_count(pointer.as_ptr()) };
        if !(3..=32).contains(&count) {
            return Err(binding("invalid namespace object count"));
        }
        let mut mapped_modules = Vec::with_capacity(count);
        let mut seen = [0; 3];
        for index in 0..count {
            let mut module = ffi::Module::new();
            let mut error = ffi::NativeError::new();
            let code =
                unsafe { ffi::cairn_math_module(pointer.as_ptr(), index, &mut module, &mut error) };
            error.result(code)?;
            if module.path.is_null() {
                return Err(binding("null namespace module path"));
            }
            let path = PathBuf::from(OsStr::from_bytes(
                unsafe { CStr::from_ptr(module.path) }.to_bytes(),
            ));
            let (role, pin) = match module.pinned_index {
                -1 => (ModuleRole::StandardDependency, None),
                i @ 0..=2 => {
                    let i = i as usize;
                    seen[i] += 1;
                    (ModuleRole::Pinned(LIBRARIES[i].library), Some(LIBRARIES[i]))
                }
                _ => return Err(binding("invalid native pinned dependency index")),
            };
            let (_, file) = read_file(&path, pin)?;
            if file.device != module.device || file.inode != module.inode {
                return Err(binding(
                    "actual mapped inode differs from hashed native file",
                ));
            }
            mapped_modules.push(ModuleObservation { role, file });
        }
        if seen != [1, 1, 1] {
            return Err(binding("pinned math dependency mapping is not one-to-one"));
        }
        Ok(Self {
            handle,
            provenance: Provenance {
                selected_files,
                mapped_modules,
                settings,
            },
        })
    }
    pub fn provenance(&self) -> &Provenance {
        &self.provenance
    }
    /// Input is already normalized in BOTH Source stages. Output is unrounded
    /// float32 similarity in full occurrence order, ready for the rank max/round owner.
    pub fn matrix_vector(&mut self, mut matrix: FullOccurrenceMatrix) -> Result<Vec<f32>, Error> {
        let layout = matrix.layout().clone();
        let mut output = vec![0.0f32; layout.passage_rows];
        if layout.operation == Operation::Empty {
            return Ok(output);
        }
        let mut error = ffi::NativeError::new();
        let code = unsafe {
            ffi::cairn_math_compute(
                self.handle.pointer.as_ptr(),
                matrix.storage().as_mut_ptr(),
                layout.full_rows,
                layout.elements,
                layout.operation as i32,
                output.as_mut_ptr(),
                output.len(),
                &mut error,
            )
        };
        error.result(code)?;
        Ok(output)
    }
}
#[cfg(test)]
mod tests;
