//! Bounded zero-copy ONNX tensor external-data closure observation.
//! It proves absence across all TensorProto-reachable v1.22.0 schema paths;
//! it does not claim generic model validity, runtime I/O safety or ORT-format support.
use crate::model_schema::{self, Message};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
const MAX_DEPTH: usize = 128;
const MAX_FIELDS: usize = 1_000_000;
const MAX_BYTES: usize = i32::MAX as usize;
const SCHEMA_SHA: &str = "a05cfbcd1370608b809c5b84c44e3198d3369036458e0b5f297e76ceaf9c4e1b";
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum Pending {
    ModelSize,
    WireOffset,
    WireType(u8),
    Varint,
    UnknownField {
        message: String,
        field: u32,
    },
    Depth,
    FieldBudget,
    MissingGraph,
    UnsupportedIr(u64),
    MissingIr,
    OrtFormat,
    ExternalTensor {
        path: String,
        entries: Vec<(usize, String)>,
    },
    UnknownLocation {
        path: String,
        value: u64,
    },
}
pub(crate) struct Closure {
    model_sha: String,
    bytes: usize,
    ir: u64,
    tensors: usize,
}
impl Closure {
    pub fn bind(&self, model: &[u8]) -> Result<Value, &'static str> {
        if model.len() != self.bytes || digest(model) != self.model_sha {
            return Err("model closure belongs to different consumed bytes");
        }
        Ok(
            json!({"kind":"ONNX-v1.22.0-all-TensorProto-paths-self-contained","schema_sha256":SCHEMA_SHA,
            "model_sha256":self.model_sha,"bytes":self.bytes,"ir_version":self.ir,"tensors":self.tensors,
            "external_tensor_files":[],"scope":"tensor-data assets only; not model validity or runtime-I/O qualification"}),
        )
    }
}
fn digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|v| format!("{v:02x}"))
        .collect()
}
#[derive(Clone, Copy)]
enum Wire<'a> {
    Integer(u64),
    Bytes(&'a [u8]),
    Fixed,
}
fn varint(bytes: &[u8], offset: &mut usize) -> Result<u64, Pending> {
    let mut value = 0u64;
    for index in 0..10 {
        let b = *bytes.get(*offset).ok_or(Pending::WireOffset)?;
        *offset = offset.checked_add(1).ok_or(Pending::WireOffset)?;
        if index == 9 && b > 1 {
            return Err(Pending::Varint);
        }
        value |= u64::from(b & 127) << (index * 7);
        if b & 128 == 0 {
            return Ok(value);
        }
    }
    Err(Pending::Varint)
}
fn next<'a>(bytes: &'a [u8], offset: &mut usize) -> Result<(u32, Wire<'a>), Pending> {
    let key = varint(bytes, offset)?;
    let field = u32::try_from(key >> 3).map_err(|_| Pending::Varint)?;
    if field == 0 || field > 0x1fff_ffff {
        return Err(Pending::Varint);
    }
    let wire = (key & 7) as u8;
    let value = match wire {
        0 => Wire::Integer(varint(bytes, offset)?),
        1 | 5 => {
            let end = offset
                .checked_add(if wire == 1 { 8 } else { 4 })
                .ok_or(Pending::WireOffset)?;
            bytes.get(*offset..end).ok_or(Pending::WireOffset)?;
            *offset = end;
            Wire::Fixed
        }
        2 => {
            let length =
                usize::try_from(varint(bytes, offset)?).map_err(|_| Pending::WireOffset)?;
            let end = offset.checked_add(length).ok_or(Pending::WireOffset)?;
            let value = bytes.get(*offset..end).ok_or(Pending::WireOffset)?;
            *offset = end;
            Wire::Bytes(value)
        }
        other => return Err(Pending::WireType(other)),
    };
    Ok((field, value))
}
struct Walk {
    fields: usize,
    tensors: usize,
    ir: Option<u64>,
    has_graph: bool,
}
impl Walk {
    /// Singular message occurrences merge by ordered concatenation, repeated
    /// children remain distinct. Last scalar wins across singular fragments.
    fn message<'a>(
        &mut self,
        kind: Message,
        fragments: &[&'a [u8]],
        depth: usize,
        path: &str,
    ) -> Result<(), Pending> {
        if depth > MAX_DEPTH {
            return Err(Pending::Depth);
        }
        let mut children: BTreeMap<u32, Vec<&'a [u8]>> = BTreeMap::new();
        let mut location = 0u64;
        let mut external = Vec::new();
        for &bytes in fragments {
            let mut offset = 0;
            while offset < bytes.len() {
                self.fields = self.fields.checked_add(1).ok_or(Pending::FieldBudget)?;
                if self.fields > MAX_FIELDS {
                    return Err(Pending::FieldBudget);
                }
                let (field, value) = next(bytes, &mut offset)?;
                if !model_schema::fields(kind).contains(&field) {
                    return Err(Pending::UnknownField {
                        message: format!("{kind:?}"),
                        field,
                    });
                }
                if kind == Message::Model && field == 1 {
                    if let Wire::Integer(v) = value {
                        self.ir = Some(v);
                    }
                }
                if kind == Message::Model && field == 7 && matches!(value, Wire::Bytes(_)) {
                    self.has_graph = true;
                }
                if kind == Message::Tensor && field == 14 {
                    if let Wire::Integer(v) = value {
                        location = v;
                    }
                }
                if kind == Message::Tensor && field == 13 {
                    if let Wire::Bytes(v) = value {
                        external.push((v.len(), digest(v)));
                    }
                }
                if model_schema::child(kind, field).is_some() {
                    // Wrong wire types are protobuf unknown fields, never hidden
                    // child messages; refuse observation rather than call complete.
                    let Wire::Bytes(child) = value else {
                        return Err(Pending::WireType(255));
                    };
                    children.entry(field).or_default().push(child);
                }
            }
        }
        if kind == Message::Tensor {
            self.tensors += 1;
            if location == 1 {
                return Err(Pending::ExternalTensor {
                    path: path.into(),
                    entries: external,
                });
            }
            if location != 0 {
                return Err(Pending::UnknownLocation {
                    path: path.into(),
                    value: location,
                });
            }
            // data_location DEFAULT means external_data metadata is not consumed
            // as an external file by ORT's HasExternalData predicate.
        }
        for (field, values) in children {
            let (child, repeated) = model_schema::child(kind, field).expect("selected child map");
            if repeated {
                for (index, value) in values.iter().enumerate() {
                    self.message(
                        child,
                        &[*value],
                        depth + 1,
                        &format!("{path}/{field}[{index}]"),
                    )?;
                }
            } else {
                self.message(child, &values, depth + 1, &format!("{path}/{field}"))?;
            }
        }
        Ok(())
    }
}
pub(crate) fn observe(model: &[u8]) -> Result<Closure, Pending> {
    if model.len() > MAX_BYTES {
        return Err(Pending::ModelSize);
    }
    // ORT's flatbuffer format is a distinct schema, never a protobuf proof.
    if model.get(4..8) == Some(b"ORTM") {
        return Err(Pending::OrtFormat);
    }
    let mut walk = Walk {
        fields: 0,
        tensors: 0,
        ir: None,
        has_graph: false,
    };
    walk.message(Message::Model, &[model], 0, "Model")?;
    let ir = walk.ir.ok_or(Pending::MissingIr)?;
    if ir == 0 || ir > 13 {
        return Err(Pending::UnsupportedIr(ir));
    }
    if !walk.has_graph {
        return Err(Pending::MissingGraph);
    }
    Ok(Closure {
        model_sha: digest(model),
        bytes: model.len(),
        ir,
        tensors: walk.tensors,
    })
}

#[cfg(test)]
mod tests;
