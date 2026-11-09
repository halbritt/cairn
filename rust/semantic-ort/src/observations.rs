//! Owned partial observations; these records never certify global defaults or node dispatch.
use crate::{contract, ffi, Error, NodeMetadata, SessionMetadata};
use std::ptr::NonNull;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MemoryDeviceType(i32);
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct AllocatorType(i32);
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MemoryType(i32);
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DeviceMemoryType(i32);
macro_rules! code { ($($t:ty),*) => { $(impl $t { pub fn code(self) -> i32 { self.0 } })* }; }
code!(
    MemoryDeviceType,
    AllocatorType,
    MemoryType,
    DeviceMemoryType
);

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct ConfigEntry {
    key: Vec<u8>,
    value: Vec<u8>,
}
impl ConfigEntry {
    pub fn key(&self) -> &[u8] {
        &self.key
    }
    pub fn value(&self) -> &[u8] {
        &self.value
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MemoryObservation {
    name: Vec<u8>,
    device_type: MemoryDeviceType,
    device_id: i32,
    allocator_type: AllocatorType,
    memory_type: MemoryType,
    device_memory_type: DeviceMemoryType,
    vendor_id: u32,
}
impl MemoryObservation {
    pub fn name(&self) -> &[u8] {
        &self.name
    }
    pub fn device_type(&self) -> MemoryDeviceType {
        self.device_type
    }
    pub fn device_id(&self) -> i32 {
        self.device_id
    }
    pub fn allocator_type(&self) -> AllocatorType {
        self.allocator_type
    }
    pub fn memory_type(&self) -> MemoryType {
        self.memory_type
    }
    pub fn device_memory_type(&self) -> DeviceMemoryType {
        self.device_memory_type
    }
    pub fn vendor_id(&self) -> u32 {
        self.vendor_id
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EpDeviceObservation {
    name: Option<Vec<u8>>,
    vendor: Option<Vec<u8>>,
}
impl EpDeviceObservation {
    pub fn name(&self) -> Option<&[u8]> {
        self.name.as_deref()
    }
    pub fn vendor(&self) -> Option<&[u8]> {
        self.vendor.as_deref()
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct IoObservation {
    ordinal: usize,
    node_name: String,
    memory: Option<MemoryObservation>,
    ep_device: Option<EpDeviceObservation>,
}
impl IoObservation {
    pub fn ordinal(&self) -> usize {
        self.ordinal
    }
    pub fn node_name(&self) -> &str {
        &self.node_name
    }
    pub fn memory(&self) -> Option<&MemoryObservation> {
        self.memory.as_ref()
    }
    pub fn ep_device(&self) -> Option<&EpDeviceObservation> {
        self.ep_device.as_ref()
    }
}

/// Constructed only from an owning session's bridge cache. Config entries are
/// an observed list (possibly empty), not a manifest of effective defaults.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionObservations {
    options_config_entries: Vec<ConfigEntry>,
    inputs: Vec<IoObservation>,
    outputs: Vec<IoObservation>,
}
impl SessionObservations {
    pub fn options_config_entries(&self) -> &[ConfigEntry] {
        &self.options_config_entries
    }
    pub fn inputs(&self) -> &[IoObservation] {
        &self.inputs
    }
    pub fn outputs(&self) -> &[IoObservation] {
        &self.outputs
    }
}

fn copy_required_bytes(raw: ffi::Bytes) -> Result<Vec<u8>, Error> {
    if raw.data.is_null() || raw.length > isize::MAX as usize {
        return Err(contract("invalid owned observation byte view"));
    }
    if raw.length == 0 {
        return Ok(Vec::new());
    }
    // Only called for live bridge cache views or owned cfg(test) fixtures.
    let mut owned = Vec::new();
    owned
        .try_reserve_exact(raw.length)
        .map_err(|e| contract(format!("observation byte allocation: {e}")))?;
    owned.extend_from_slice(unsafe { std::slice::from_raw_parts(raw.data, raw.length) });
    Ok(owned)
}
fn copy_optional_bytes(raw: ffi::Bytes) -> Result<Option<Vec<u8>>, Error> {
    if raw.data.is_null() {
        if raw.length != 0 {
            return Err(contract("null observation bytes with nonzero length"));
        }
        Ok(None)
    } else {
        copy_required_bytes(raw).map(Some)
    }
}
fn decode_config(raw: &ffi::ConfigView) -> Result<Vec<ConfigEntry>, Error> {
    if raw.observed != 1
        || raw.reserved != 0
        || raw.count
            > isize::MAX as usize
                / std::mem::size_of::<ConfigEntry>().max(std::mem::size_of::<ffi::ConfigEntry>())
        || (raw.count != 0 && raw.entries.is_null())
    {
        return Err(contract(
            "configuration entries not observed or invalid view/count",
        ));
    }
    let rows = if raw.count == 0 {
        &[][..]
    } else {
        unsafe { std::slice::from_raw_parts(raw.entries, raw.count) }
    };
    let mut entries = Vec::new();
    entries
        .try_reserve_exact(rows.len())
        .map_err(|e| contract(format!("config observation allocation: {e}")))?;
    for r in rows {
        entries.push(ConfigEntry {
            key: copy_required_bytes(r.key)?,
            value: copy_required_bytes(r.value)?,
        });
    }
    // GetKeyValuePairs enumeration is not canonical. Sort complete byte pairs;
    // keep every duplicate rather than overwrite keys or collide lossy UTF-8.
    entries.sort_unstable();
    Ok(entries)
}
fn decode_memory(raw: &ffi::MemoryObservation) -> Result<Option<MemoryObservation>, Error> {
    if raw.reserved != 0 {
        return Err(contract("nonzero memory observation reserved field"));
    }
    match raw.present {
        0 if raw.name.data.is_null()
            && raw.name.length == 0
            && [
                raw.device_type,
                raw.device_id,
                raw.allocator_type,
                raw.memory_type,
                raw.device_memory_type,
            ]
            .iter()
            .all(|v| *v == 0)
            && raw.vendor_id == 0 =>
        {
            Ok(None)
        }
        1 => Ok(Some(MemoryObservation {
            name: copy_required_bytes(raw.name)?,
            device_type: MemoryDeviceType(raw.device_type),
            device_id: raw.device_id,
            allocator_type: AllocatorType(raw.allocator_type),
            memory_type: MemoryType(raw.memory_type),
            device_memory_type: DeviceMemoryType(raw.device_memory_type),
            vendor_id: raw.vendor_id,
        })),
        _ => Err(contract("invalid memory observation presence/payload")),
    }
}
fn decode_ep(raw: &ffi::EpObservation) -> Result<Option<EpDeviceObservation>, Error> {
    if raw.reserved != 0 {
        return Err(contract("nonzero EP observation reserved field"));
    }
    match raw.present {
        0 if raw.name.data.is_null()
            && raw.name.length == 0
            && raw.vendor.data.is_null()
            && raw.vendor.length == 0 =>
        {
            Ok(None)
        }
        1 => Ok(Some(EpDeviceObservation {
            name: copy_optional_bytes(raw.name)?,
            vendor: copy_optional_bytes(raw.vendor)?,
        })),
        _ => Err(contract("invalid EP observation presence/payload")),
    }
}
fn decode_io(
    nodes: &[NodeMetadata],
    raw: &[ffi::IoObservation],
) -> Result<Vec<IoObservation>, Error> {
    if nodes.len() != raw.len() || nodes.len() > 64 {
        return Err(contract(
            "IO observations differ from owning metadata count",
        ));
    }
    let mut owned = Vec::new();
    owned
        .try_reserve_exact(nodes.len())
        .map_err(|e| contract(format!("I/O evidence allocation: {e}")))?;
    for (ordinal, (node, r)) in nodes.iter().zip(raw).enumerate() {
        let name = copy_required_bytes(r.node_name)?;
        if r.ordinal != ordinal || name != node.name.as_bytes() {
            return Err(contract("IO observation ordinal/name mismatch"));
        }
        owned.push(IoObservation {
            ordinal,
            node_name: String::from_utf8(name)
                .map_err(|_| contract("observation metadata name is not UTF-8"))?,
            memory: decode_memory(&r.memory)?,
            ep_device: decode_ep(&r.ep)?,
        });
    }
    Ok(owned)
}

pub(crate) fn read(
    pointer: NonNull<ffi::Session>,
    metadata: &SessionMetadata,
) -> Result<SessionObservations, Error> {
    let mut raw = ffi::ConfigView::default();
    let mut error = ffi::NativeError::new();
    let status =
        unsafe { ffi::cairn_ort_config_observation(pointer.as_ptr(), &mut raw, &mut error) };
    error.result(status)?;
    let options_config_entries = decode_config(&raw)?;
    let read_io = |input, nodes: &[NodeMetadata]| -> Result<Vec<IoObservation>, Error> {
        if nodes.len() > 64 {
            return Err(contract("IO observation count bound"));
        }
        let mut rows = Vec::new();
        rows.try_reserve_exact(nodes.len())
            .map_err(|e| contract(format!("I/O observation allocation: {e}")))?;
        for ordinal in 0..nodes.len() {
            let mut row = ffi::IoObservation::default();
            let mut error = ffi::NativeError::new();
            let status = unsafe {
                ffi::cairn_ort_io_observation(
                    pointer.as_ptr(),
                    input,
                    ordinal,
                    &mut row,
                    &mut error,
                )
            };
            error.result(status)?;
            rows.push(row);
        }
        decode_io(nodes, &rows)
    };
    Ok(SessionObservations {
        options_config_entries,
        inputs: read_io(1, &metadata.inputs)?,
        outputs: read_io(0, &metadata.outputs)?,
    })
}
#[cfg(test)]
mod tests;
