use crate::Error;
use std::ffi::{c_char, c_int, CStr};
#[repr(C)]
pub(crate) struct Runtime {
    _opaque: [u8; 0],
}
#[repr(C)]
pub(crate) struct NativeError {
    code: i32,
    stage: *const c_char,
    message: *mut c_char,
    copy_failed: i32,
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
        let stage = if self.stage.is_null() {
            String::new()
        } else {
            unsafe { CStr::from_ptr(self.stage) }
                .to_string_lossy()
                .into_owned()
        };
        let detail = if self.message.is_null() {
            Vec::new()
        } else {
            unsafe { CStr::from_ptr(self.message) }.to_bytes().to_vec()
        };
        Err(Error::Native {
            stage,
            code: self.code,
            detail,
            error_copy_failed: self.copy_failed != 0,
        })
    }
}
impl Drop for NativeError {
    fn drop(&mut self) {
        unsafe { cairn_math_error_release(self) }
    }
}
#[repr(C)]
pub(crate) struct Bytes {
    pub bytes: *const u8,
    pub length: usize,
}
#[repr(C)]
pub(crate) struct Module {
    pub path: *const c_char,
    pub device: u64,
    pub inode: u64,
    pub pinned_index: i32,
}
impl Module {
    pub fn new() -> Self {
        Self {
            path: std::ptr::null(),
            device: 0,
            inode: 0,
            pinned_index: -1,
        }
    }
}
#[repr(C)]
pub(crate) struct Settings {
    pub config: *const c_char,
    pub core: *const c_char,
    pub threads: i32,
    pub parallel: i32,
}
impl Settings {
    pub fn new() -> Self {
        Self {
            config: std::ptr::null(),
            core: std::ptr::null(),
            threads: 0,
            parallel: 0,
        }
    }
}
extern "C" {
    fn cairn_math_error_release(error: *mut NativeError);
    pub fn cairn_math_open(
        inputs: *const Bytes,
        out: *mut *mut Runtime,
        settings: *mut Settings,
        error: *mut NativeError,
    ) -> c_int;
    pub fn cairn_math_close(runtime: *mut Runtime, error: *mut NativeError) -> c_int;
    pub fn cairn_math_module_count(runtime: *const Runtime) -> usize;
    pub fn cairn_math_module(
        runtime: *const Runtime,
        index: usize,
        out: *mut Module,
        error: *mut NativeError,
    ) -> c_int;
    pub fn cairn_math_compute(
        runtime: *mut Runtime,
        matrix: *const f32,
        full_rows: usize,
        elements: usize,
        operation: i32,
        output: *mut f32,
        output_length: usize,
        error: *mut NativeError,
    ) -> c_int;
}
