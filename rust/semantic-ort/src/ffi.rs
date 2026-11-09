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
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct Bytes {
    pub data: *const u8,
    pub length: usize,
}
impl Default for Bytes {
    fn default() -> Self {
        Self {
            data: std::ptr::null(),
            length: 0,
        }
    }
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub(crate) struct ConfigEntry {
    pub key: Bytes,
    pub value: Bytes,
}
#[repr(C)]
pub(crate) struct ConfigView {
    pub observed: i32,
    pub reserved: i32,
    pub count: usize,
    pub entries: *const ConfigEntry,
}
impl Default for ConfigView {
    fn default() -> Self {
        Self {
            observed: 0,
            reserved: 0,
            count: 0,
            entries: std::ptr::null(),
        }
    }
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub(crate) struct MemoryObservation {
    pub present: i32,
    pub device_type: i32,
    pub device_id: i32,
    pub allocator_type: i32,
    pub memory_type: i32,
    pub device_memory_type: i32,
    pub vendor_id: u32,
    pub reserved: u32,
    pub name: Bytes,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub(crate) struct EpObservation {
    pub present: i32,
    pub reserved: i32,
    pub name: Bytes,
    pub vendor: Bytes,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub(crate) struct IoObservation {
    pub ordinal: usize,
    pub node_name: Bytes,
    pub memory: MemoryObservation,
    pub ep: EpObservation,
}
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub(crate) struct AssignedNode {
    pub name: Bytes,
    pub domain: Bytes,
    pub operator_type: Bytes,
}
#[repr(C)]
#[derive(Clone, Copy)]
pub(crate) struct AssignedSubgraph {
    pub provider: Bytes,
    pub node_count: usize,
    pub nodes: *const AssignedNode,
}
#[repr(C)]
pub(crate) struct AssignmentView {
    pub observed: i32,
    pub reserved: i32,
    pub count: usize,
    pub subgraphs: *const AssignedSubgraph,
}
impl Default for AssignmentView {
    fn default() -> Self {
        Self {
            observed: 0,
            reserved: 0,
            count: 0,
            subgraphs: std::ptr::null(),
        }
    }
}
const _: [(); 48] = [(); std::mem::size_of::<AssignedNode>()];
const _: [(); 32] = [(); std::mem::size_of::<AssignedSubgraph>()];
const _: [(); 24] = [(); std::mem::size_of::<AssignmentView>()];
macro_rules! offsets {
    ($record:ty; $($field:ident = $offset:expr),+ $(,)?) => {
        $(const _: [(); $offset] = [(); std::mem::offset_of!($record, $field)];)+
    };
}
offsets!(AssignedNode; name=0, domain=16, operator_type=32);
offsets!(AssignedSubgraph; provider=0, node_count=16, nodes=24);
offsets!(AssignmentView; observed=0, reserved=4, count=8, subgraphs=16);
offsets!(ConfigView; observed=0, reserved=4);
offsets!(MemoryObservation; present=0, device_type=4, device_id=8, allocator_type=12,
    memory_type=16, device_memory_type=20, vendor_id=24, reserved=28);
offsets!(EpObservation; present=0, reserved=4);
const _: [(); 16] = [(); std::mem::size_of::<Bytes>()];
const _: [(); 32] = [(); std::mem::size_of::<ConfigEntry>()];
const _: [(); 24] = [(); std::mem::size_of::<ConfigView>()];
const _: [(); 48] = [(); std::mem::size_of::<MemoryObservation>()];
const _: [(); 40] = [(); std::mem::size_of::<EpObservation>()];
const _: [(); 112] = [(); std::mem::size_of::<IoObservation>()];
const _: [(); 8] = [(); std::mem::offset_of!(ConfigView, count)];
const _: [(); 16] = [(); std::mem::offset_of!(ConfigView, entries)];
const _: [(); 32] = [(); std::mem::offset_of!(MemoryObservation, name)];
const _: [(); 8] = [(); std::mem::offset_of!(EpObservation, name)];
const _: [(); 24] = [(); std::mem::offset_of!(EpObservation, vendor)];
const _: [(); 8] = [(); std::mem::offset_of!(IoObservation, node_name)];
const _: [(); 24] = [(); std::mem::offset_of!(IoObservation, memory)];
const _: [(); 72] = [(); std::mem::offset_of!(IoObservation, ep)];
extern "C" {
    pub fn cairn_ort_assignment_observation(
        session: *const Session,
        out: *mut AssignmentView,
        error: *mut NativeError,
    ) -> c_int;
    pub fn cairn_ort_config_observation(
        session: *const Session,
        out: *mut ConfigView,
        error: *mut NativeError,
    ) -> c_int;
    pub fn cairn_ort_io_observation(
        session: *const Session,
        input: c_int,
        index: usize,
        out: *mut IoObservation,
        error: *mut NativeError,
    ) -> c_int;
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
