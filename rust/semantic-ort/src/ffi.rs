use crate::Error;
use std::ffi::{c_char, c_int, CStr};

#[repr(C)]
pub(crate) struct Runtime {
    _opaque: [u8; 0],
}
#[repr(C)]
pub(crate) struct Session {
    _opaque: [u8; 0],
}
#[repr(C)]
pub(crate) struct NativeError {
    pub code: i32,
    pub stage: *const c_char,
    pub message: *mut c_char,
    pub copy_failed: i32,
}
impl NativeError {
    pub fn new() -> Self {
        Self {
            code: 0,
            stage: std::ptr::null(),
            message: std::ptr::null_mut(),
            copy_failed: 0,
        }
    }
    pub fn result(&self, code: c_int) -> Result<(), Error> {
        if code == 0 {
            return Ok(());
        }
        // The bridge owns NUL-terminated strings until this guard is dropped.
        let text = |p: *const c_char| {
            if p.is_null() {
                String::new()
            } else {
                unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned()
            }
        };
        Err(Error::Native {
            stage: text(self.stage),
            code: self.code,
            detail: if self.message.is_null() {
                Vec::new()
            } else {
                unsafe { CStr::from_ptr(self.message) }.to_bytes().to_vec()
            },
            error_copy_failed: self.copy_failed != 0,
        })
    }
}
impl Drop for NativeError {
    fn drop(&mut self) {
        unsafe { cairn_ort_error_release(self) }
    }
}
#[repr(C)]
#[derive(Default)]
pub(crate) struct Mapping {
    pub device: u64,
    pub inode: u64,
    pub bytes: u64,
    pub fd: i32,
}
#[repr(C)]
#[derive(Default)]
pub(crate) struct Options {
    pub execution_mode: i32,
    pub memory_pattern: i32,
}
#[repr(C)]
pub(crate) struct Node {
    pub name: *const c_char,
    pub kind: i32,
    pub ort_type: i32,
    pub rank: usize,
    pub dimensions: *const i64,
}
impl Node {
    pub fn new() -> Self {
        Self {
            name: std::ptr::null(),
            kind: 0,
            ort_type: 0,
            rank: 0,
            dimensions: std::ptr::null(),
        }
    }
}
#[repr(C)]
pub(crate) struct Output {
    pub rank: usize,
    pub dimensions: [i64; 3],
    pub count: usize,
    pub bits: *mut u32,
}
const _: [(); 48] = [(); std::mem::size_of::<Output>()];
impl Output {
    pub fn new() -> Self {
        Self {
            rank: 0,
            dimensions: [0; 3],
            count: 0,
            bits: std::ptr::null_mut(),
        }
    }
}
impl Drop for Output {
    fn drop(&mut self) {
        unsafe { cairn_ort_output_release(self) }
    }
}
extern "C" {
    fn cairn_ort_error_release(error: *mut NativeError);
    pub fn cairn_ort_runtime_open(
        bytes: *const u8,
        length: usize,
        out: *mut *mut Runtime,
        mapping: *mut Mapping,
        error: *mut NativeError,
    ) -> c_int;
    pub fn cairn_ort_runtime_close(runtime: *mut Runtime, error: *mut NativeError) -> c_int;
    pub fn cairn_ort_session_open(
        runtime: *mut Runtime,
        model: *const u8,
        length: usize,
        out: *mut *mut Session,
        options: *mut Options,
        error: *mut NativeError,
    ) -> c_int;
    pub fn cairn_ort_session_close(session: *mut Session);
    pub fn cairn_ort_node_count(session: *const Session, input: c_int) -> usize;
    pub fn cairn_ort_node(
        session: *const Session,
        input: c_int,
        index: usize,
        out: *mut Node,
        error: *mut NativeError,
    ) -> c_int;
    pub fn cairn_ort_run(
        session: *mut Session,
        ids: *const i64,
        mask: *const i64,
        types: *const i64,
        sequence: usize,
        out: *mut Output,
        error: *mut NativeError,
    ) -> c_int;
    fn cairn_ort_output_release(out: *mut Output);
}
