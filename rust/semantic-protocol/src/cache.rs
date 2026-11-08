//! Pure decoded cache policy. Identity and stream phase are supplied inputs,
//! not evidence of an initialized backend. No JSON serializer is provided.

use base64::engine::general_purpose::{GeneralPurpose, GeneralPurposeConfig, STANDARD};
use base64::{alphabet, Engine};

use crate::hash::HashAdmittedStream;
use crate::{FailureKind, IrError, PyObject, PyString, Value};

pub const CACHE_LIMIT: u128 = 600 * 1024;
pub const VECTOR_COMPONENTS: usize = 384;
pub const VECTOR_BYTES: usize = VECTOR_COMPONENTS * 4;
pub const ENCODED_CODEPOINTS: usize = 2048;
const FORMAT: &str = "cairn.passage-vectors/1";
const SOURCE_BASE64: GeneralPurpose = GeneralPurpose::new(
    &alphabet::STANDARD,
    GeneralPurposeConfig::new().with_decode_allow_trailing_bits(true),
);

/// No digest syntax or model initialization is asserted by this type.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SuppliedIdentity(PyString);

impl SuppliedIdentity {
    pub fn new(identity: PyString) -> Self {
        Self(identity)
    }
    pub fn value(&self) -> &PyString {
        &self.0
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MeasurementBoundary {
    FiniteNonzeroFloat,
    IntegerMagnitudeBeyond64Bits,
    ContainerBeyondChildLevel,
}

/// The original state remains unmeasured. No caller count can close this stage.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CacheEnvelopeNeedsSourceMeasure<'a> {
    input: &'a Value,
    boundary: MeasurementBoundary,
}

impl<'a> CacheEnvelopeNeedsSourceMeasure<'a> {
    pub fn input(&self) -> &'a Value {
        self.input
    }
    pub fn boundary(&self) -> MeasurementBoundary {
        self.boundary
    }
}

fn string_size(text: &PyString) -> u128 {
    2 + text
        .codepoints()
        .iter()
        .map(|&cp| match cp {
            0x22 | 0x5c | 0x08 | 0x0c | 0x0a | 0x0d | 0x09 => 2u128,
            0x20..=0x7e => 1,
            0..=0xffff => 6,
            _ => 12,
        })
        .sum::<u128>()
}

fn measure(value: &Value, container_depth: usize) -> Result<u128, MeasurementBoundary> {
    match value {
        Value::Null => Ok(4),
        Value::Bool(true) => Ok(4),
        Value::Bool(false) => Ok(5),
        Value::String(text) => Ok(string_size(text)),
        Value::Integer(integer) => {
            let limbs = integer.magnitude();
            if limbs.len() > 2 {
                return Err(MeasurementBoundary::IntegerMagnitudeBeyond64Bits);
            }
            let mut magnitude = u64::from(limbs.first().copied().unwrap_or(0))
                | (u64::from(limbs.get(1).copied().unwrap_or(0)) << 32);
            let mut digits = 1 + u128::from(integer.is_negative());
            while magnitude >= 10 {
                magnitude /= 10;
                digits += 1;
            }
            Ok(digits)
        }
        Value::FloatBits(bits) => {
            let magnitude = bits & 0x7fff_ffff_ffff_ffff;
            if magnitude == 0 {
                Ok(if bits >> 63 == 0 { 3 } else { 4 })
            } else if magnitude >> 52 == 0x7ff {
                if magnitude & 0x000f_ffff_ffff_ffff != 0 {
                    Ok(3) // Every NaN, including negative/payload NaNs, is "NaN".
                } else {
                    Ok(if bits >> 63 == 0 { 8 } else { 9 })
                }
            } else {
                Err(MeasurementBoundary::FiniteNonzeroFloat)
            }
        }
        Value::Array(values) => {
            if container_depth > 1 {
                return Err(MeasurementBoundary::ContainerBeyondChildLevel);
            }
            let mut size = 2;
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    size += 2; // ", "
                }
                size += measure(value, container_depth + 1)?;
            }
            Ok(size)
        }
        Value::Object(object) => {
            if container_depth > 1 {
                return Err(MeasurementBoundary::ContainerBeyondChildLevel);
            }
            let mut size = 2;
            for (index, (key, value)) in object.entries().iter().enumerate() {
                if index != 0 {
                    size += 2;
                }
                size += string_size(key) + 2 + measure(value, container_depth + 1)?;
            }
            Ok(size)
        }
    }
}

/// Exact default json.dumps/ensure_ascii logical length on the selected domain.
/// Root containers have depth 0 and child containers depth 1. The counter never
/// shortcuts on CACHE_LIMIT: a later unsupported value must remain pending.
/// u128 holds the exact length of this shallow in-memory domain on the selected
/// 64-bit host; no saturation, float conversion or serialization is used.
pub fn measure_envelope(input: &Value) -> Result<u128, CacheEnvelopeNeedsSourceMeasure<'_>> {
    measure(input, 0).map_err(|boundary| CacheEnvelopeNeedsSourceMeasure { input, boundary })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Base64Failure {
    NonAscii,
    InvalidEncoding,
}

/// CPython 3.11.15 strict Base64 compatibility over the established engine.
/// Only an all-'=' suffix after nonempty complete data quartets is stripped.
pub fn decode_source_base64(encoded: &PyString) -> Result<Vec<u8>, Base64Failure> {
    let mut bytes = Vec::with_capacity(encoded.len());
    for &cp in encoded.codepoints() {
        if cp > 0x7f {
            return Err(Base64Failure::NonAscii);
        }
        bytes.push(cp as u8);
    }
    if bytes.first() == Some(&b'=') {
        return Err(Base64Failure::InvalidEncoding);
    }
    let mut input = bytes.as_slice();
    if let Some(first) = bytes.iter().position(|&byte| byte == b'=') {
        if first > 0 && first % 4 == 0 && bytes[first..].iter().all(|&byte| byte == b'=') {
            input = &bytes[..first];
        }
    }
    SOURCE_BASE64
        .decode(input)
        .map_err(|_| Base64Failure::InvalidEncoding)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CacheSite {
    Format,
    EnvelopeSize,
    VectorMap,
    EntryKey(usize),
    EntryEncoded(usize),
    EntryBase64(usize),
    EntryBytes(usize),
    EntryVector(usize),
    SnapshotVector(usize),
    SnapshotCount,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CacheRefusal {
    pub site: CacheSite,
    pub kind: FailureKind,
}

fn refuse(site: CacheSite) -> CacheRefusal {
    CacheRefusal {
        site,
        kind: FailureKind::ValueError,
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CacheEntry {
    key: PyString,
    bits: Vec<u32>,
}

impl CacheEntry {
    pub fn key(&self) -> &PyString {
        &self.key
    }
    pub fn bits(&self) -> &[u32] {
        &self.bits
    }
}

/// Payload state only. It carries no initialized-identity/model provenance.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CacheState {
    entries: Vec<CacheEntry>,
}

impl CacheState {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn entries(&self) -> &[CacheEntry] {
        &self.entries
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RestoreOutcome<'a> {
    NoOp,
    IgnoredOtherModel,
    Replaced,
    NeedsSourceMeasure(CacheEnvelopeNeedsSourceMeasure<'a>),
}

fn finite(bits: u32) -> bool {
    bits & 0x7f80_0000 != 0x7f80_0000
}

/// Standalone payload policy; this never substitutes for Source's constructor.
/// All entries are validated into temporary storage before one replacement.
pub fn restore_payload<'a>(
    old: &mut CacheState,
    input: &'a Value,
    identity: &SuppliedIdentity,
) -> Result<RestoreOutcome<'a>, CacheRefusal> {
    if matches!(input, Value::Null) {
        return Ok(RestoreOutcome::NoOp);
    }
    let state = match input {
        Value::Object(state)
            if state.entries().len() == 3
                && state.get_str("model_sha256").is_some()
                && state.get_str("vectors").is_some()
                && matches!(state.get_str("format"), Some(Value::String(format)) if format.equals_str(FORMAT)) =>
        {
            state
        }
        _ => return Err(refuse(CacheSite::Format)),
    };
    let size = match measure_envelope(input) {
        Ok(size) => size,
        Err(pending) => return Ok(RestoreOutcome::NeedsSourceMeasure(pending)),
    };
    if size > CACHE_LIMIT {
        return Err(refuse(CacheSite::EnvelopeSize));
    }
    // Python != between a string and any other IR tag is true. No type guard.
    if !matches!(state.get_str("model_sha256"), Some(Value::String(model)) if model == identity.value())
    {
        return Ok(RestoreOutcome::IgnoredOtherModel);
    }
    let vectors = match state.get_str("vectors") {
        Some(Value::Object(vectors)) if vectors.entries().len() <= 128 => vectors,
        _ => return Err(refuse(CacheSite::VectorMap)),
    };
    let mut replacement = Vec::with_capacity(vectors.entries().len());
    for (index, (key, encoded)) in vectors.entries().iter().enumerate() {
        if key.len() != 64
            || !key
                .codepoints()
                .iter()
                .all(|cp| matches!(*cp, 0x30..=0x39 | 0x61..=0x66))
        {
            return Err(refuse(CacheSite::EntryKey(index)));
        }
        let encoded = match encoded {
            Value::String(encoded) if encoded.len() == ENCODED_CODEPOINTS => encoded,
            _ => return Err(refuse(CacheSite::EntryEncoded(index))),
        };
        let raw =
            decode_source_base64(encoded).map_err(|_| refuse(CacheSite::EntryBase64(index)))?;
        if raw.len() != VECTOR_BYTES {
            return Err(refuse(CacheSite::EntryBytes(index)));
        }
        let bits: Vec<u32> = raw
            .chunks_exact(4)
            .map(|chunk| u32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]))
            .collect();
        if !bits.iter().copied().all(finite) || !bits.iter().any(|bits| bits & 0x7fff_ffff != 0) {
            return Err(refuse(CacheSite::EntryVector(index)));
        }
        replacement.push(CacheEntry {
            key: key.clone(),
            bits,
        });
    }
    old.entries = replacement;
    Ok(RestoreOutcome::Replaced)
}

/// This integration entry point requires successful prefix/hash/late-policy
/// admission. A missing key differs from present Null, though both are no-op
/// for Fresh policy. Active present keys cannot construct this capability.
pub fn restore_stream_cache<'a>(
    old: &mut CacheState,
    admitted: &HashAdmittedStream<'a>,
    identity: &SuppliedIdentity,
) -> Result<RestoreOutcome<'a>, CacheRefusal> {
    match admitted.cache() {
        None => Ok(RestoreOutcome::NoOp),
        Some(input) => restore_payload(old, input, identity),
    }
}

/// Unique-key ordered snapshot input, corresponding to Source's dict invariant.
/// Explicit u32 bits represent supplied float32 components, not a native dtype
/// attestation. Shape/finiteness are checked in snapshot order, not here.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SnapshotEntries(Vec<(PyString, Vec<u32>)>);

impl SnapshotEntries {
    pub fn new(entries: Vec<(PyString, Vec<u32>)>) -> Result<Self, IrError> {
        for (duplicate, (key, _)) in entries.iter().enumerate() {
            if let Some(first) = entries[..duplicate]
                .iter()
                .position(|(prior, _)| prior == key)
            {
                return Err(IrError::DuplicateObjectKey { first, duplicate });
            }
        }
        Ok(Self(entries))
    }
    pub fn entries(&self) -> &[(PyString, Vec<u32>)] {
        &self.0
    }
}

/// Decoded cache Value only: no general JSON encoder or worker reply path.
/// All vectors precede aggregate count/size checks; zero vectors and arbitrary
/// string keys are intentionally permitted by snapshot, unlike restore.
pub fn snapshot_payload(
    input: &SnapshotEntries,
    identity: &SuppliedIdentity,
) -> Result<Value, CacheRefusal> {
    let mut entries = Vec::with_capacity(input.0.len());
    for (index, (key, bits)) in input.0.iter().enumerate() {
        if bits.len() != VECTOR_COMPONENTS || !bits.iter().copied().all(finite) {
            return Err(refuse(CacheSite::SnapshotVector(index)));
        }
        let mut raw = Vec::with_capacity(VECTOR_BYTES);
        for component in bits {
            raw.extend_from_slice(&component.to_le_bytes());
        }
        entries.push((
            key.clone(),
            Value::String(PyString::from(STANDARD.encode(raw).as_str())),
        ));
    }
    if entries.len() > 128 {
        return Err(refuse(CacheSite::SnapshotCount));
    }
    // Input keys are unique; these three fixed keys are distinct. Every value
    // is a string or the sole child object, so measurement is always supported.
    let state = Value::Object(PyObject(vec![
        (
            PyString::from("format"),
            Value::String(PyString::from(FORMAT)),
        ),
        (
            PyString::from("model_sha256"),
            Value::String(identity.value().clone()),
        ),
        (PyString::from("vectors"), Value::Object(PyObject(entries))),
    ]));
    let size = measure_envelope(&state).expect("snapshot contains only two-level string objects");
    if size > CACHE_LIMIT {
        return Err(refuse(CacheSite::EnvelopeSize));
    }
    Ok(state)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hash::{validate_stream_hashes, StreamPhase};
    use crate::{CachePolicy, PyInteger};

    fn text(value: &str) -> Value {
        Value::String(value.into())
    }
    fn object(fields: Vec<(&str, Value)>) -> Value {
        map(fields
            .into_iter()
            .map(|(key, value)| (key.into(), value))
            .collect())
    }
    fn map(entries: Vec<(PyString, Value)>) -> Value {
        Value::Object(PyObject::new(entries).unwrap())
    }
    fn identity() -> SuppliedIdentity {
        SuppliedIdentity::new("self".into())
    }
    fn state(model: Value, vectors: Value) -> Value {
        object(vec![
            ("format", text(FORMAT)),
            ("model_sha256", model),
            ("vectors", vectors),
        ])
    }
    fn key(index: usize) -> PyString {
        format!("{index:064x}").as_str().into()
    }
    // SDP2-LE: literal independently specified by the accepted criteria: the
    // first component is 1.0, followed by 383 positive zeros, little-endian.
    fn literal_one() -> String {
        format!("AACAPw{}", "A".repeat(2042))
    }
    fn vector_map(count: usize) -> Value {
        map((0..count)
            .rev()
            .map(|i| (key(i), text(&literal_one())))
            .collect())
    }
    fn seed() -> CacheState {
        let mut cache = CacheState::new();
        assert_eq!(
            restore_payload(&mut cache, &state(text("self"), vector_map(1)), &identity()).unwrap(),
            RestoreOutcome::Replaced
        );
        cache
    }
    fn refusal_unchanged(input: &Value, site: CacheSite) {
        let mut cache = seed();
        let before = cache.clone();
        assert_eq!(
            restore_payload(&mut cache, input, &identity()),
            Err(refuse(site))
        );
        assert_eq!(cache, before);
    }
    fn pending_unchanged(input: &Value, boundary: MeasurementBoundary) {
        let mut cache = seed();
        let before = cache.clone();
        let RestoreOutcome::NeedsSourceMeasure(pending) =
            restore_payload(&mut cache, input, &identity()).unwrap()
        else {
            panic!("SDP2 expected unresolved Source measurement")
        };
        assert!(std::ptr::eq(pending.input(), input));
        assert_eq!(pending.boundary(), boundary);
        assert_eq!(cache, before);
    }

    #[test]
    fn sdp2_base64_literal_python_compatibility() {
        // SDP2-B64 source-derived CPython 3.11.15 cases, no executed oracle.
        for (encoded, raw) in [
            ("", b"".as_slice()),
            ("TWFu", b"Man".as_slice()),
            ("TWFu=", b"Man".as_slice()),
            ("TWFu====", b"Man".as_slice()),
            ("TWE=", b"Ma".as_slice()),
            ("TQ==", b"M".as_slice()),
            ("Zh==", b"f".as_slice()),
        ] {
            assert_eq!(
                decode_source_base64(&encoded.into()).unwrap(),
                raw,
                "{encoded}"
            );
        }
        for encoded in [
            "=",
            "==",
            "=TWFu",
            "TWE==",
            "TQ===",
            "TQ=",
            "TWE",
            "T",
            "TW=E",
            "TWFu=TQ==",
            "T WFu",
            "TWFu\n",
            "-",
            "_",
            "é",
        ] {
            assert!(decode_source_base64(&encoded.into()).is_err(), "{encoded}");
        }
        assert_eq!(
            decode_source_base64(&PyString::from_codepoints(vec![0xd800]).unwrap()),
            Err(Base64Failure::NonAscii)
        );
    }

    #[test]
    fn sdp2_measure_ascii_escapes_numbers_and_shallow_containers() {
        // SDP2-SIZE: independent ensure_ascii token-length sums.
        let escaped = PyString::from_codepoints(vec![
            0x20, 0x21, 0x22, 0x5c, 0x08, 0x0c, 0x0a, 0x0d, 0x09, 0x1f, 0x7f, 0xd800, 0x1f642,
        ])
        .unwrap();
        assert_eq!(measure_envelope(&Value::String(escaped)).unwrap(), 48);
        assert_eq!(measure_envelope(&text("")).unwrap(), 2);
        for (negative, limbs, length) in [
            (false, vec![], 1),
            (true, vec![0], 1),
            (false, vec![10], 2),
            (false, vec![u32::MAX], 10),
            (false, vec![u32::MAX, u32::MAX], 20),
            (true, vec![u32::MAX, u32::MAX], 21),
        ] {
            assert_eq!(
                measure_envelope(&Value::Integer(PyInteger::new(negative, limbs))).unwrap(),
                length
            );
        }
        let floats = Value::Array(vec![
            Value::Null,
            Value::Bool(true),
            Value::Bool(false),
            Value::FloatBits(0),
            Value::FloatBits(0x8000_0000_0000_0000),
            Value::FloatBits(0x7ff0_0000_0000_0000),
            Value::FloatBits(0xfff0_0000_0000_0000),
            Value::FloatBits(0x7ff8_0000_0000_0001),
            Value::FloatBits(0xfff0_0000_0000_0001),
        ]);
        assert_eq!(measure_envelope(&floats).unwrap(), 61);
        assert_eq!(
            measure_envelope(&object(vec![
                ("a", Value::Null),
                ("b", Value::Array(vec![]))
            ]))
            .unwrap(),
            20
        );
        assert_eq!(
            measure_envelope(&Value::FloatBits(0xfff8_1234_5678_9012)).unwrap(),
            3
        );
        assert_eq!(
            measure_envelope(&Value::Array(vec![Value::Array(vec![])])).unwrap(),
            4
        );
        assert_eq!(
            measure_envelope(&Value::FloatBits(1))
                .unwrap_err()
                .boundary(),
            MeasurementBoundary::FiniteNonzeroFloat
        );
    }

    #[test]
    fn sdp2_restore_format_and_exact_size_precede_identity() {
        let mut cache = seed();
        let before = cache.clone();
        assert_eq!(
            restore_payload(&mut cache, &Value::Null, &identity()).unwrap(),
            RestoreOutcome::NoOp
        );
        assert_eq!(cache, before);
        for malformed in [
            Value::Bool(false),
            object(vec![]),
            object(vec![
                ("format", text("wrong")),
                ("model_sha256", Value::FloatBits(1)),
                ("vectors", text(&"A".repeat(614401))),
            ]),
            object(vec![
                ("format", text(FORMAT)),
                ("model_sha256", text("other")),
                ("vectors", Value::Null),
                ("extra", Value::Null),
            ]),
        ] {
            refusal_unchanged(&malformed, CacheSite::Format);
        }
        // Literal default-JSON envelope has 77 bytes before vector-string data.
        let literal =
            r#"{"format": "cairn.passage-vectors/1", "model_sha256": "other", "vectors": ""}"#;
        assert_eq!(literal.len(), 77);
        let exact = state(text("other"), text(&"A".repeat(614400 - 77)));
        assert_eq!(measure_envelope(&exact).unwrap(), 614400);
        assert_eq!(
            restore_payload(&mut cache, &exact, &identity()).unwrap(),
            RestoreOutcome::IgnoredOtherModel
        );
        assert_eq!(cache, before);
        assert_eq!(
            restore_payload(&mut cache, &exact, &SuppliedIdentity::new("other".into())),
            Err(refuse(CacheSite::VectorMap))
        );
        assert_eq!(cache, before);
        let over = state(text("other"), text(&"A".repeat(614401 - 77)));
        assert_eq!(measure_envelope(&over).unwrap(), 614401);
        refusal_unchanged(&over, CacheSite::EnvelopeSize);
    }

    #[test]
    fn sdp2_source_measure_pending_precedes_size_identity_and_vectors() {
        // Q4: unsupported vectors AFTER an already-overbound supported model.
        pending_unchanged(
            &state(text(&"A".repeat(614401)), Value::FloatBits(1)),
            MeasurementBoundary::FiniteNonzeroFloat,
        );
        pending_unchanged(
            &state(text("other"), Value::FloatBits(1)),
            MeasurementBoundary::FiniteNonzeroFloat,
        );
        // Unsupported identity precedes both a would-be mismatch and bad vectors.
        pending_unchanged(
            &state(Value::FloatBits(0x3ff0_0000_0000_0000), Value::Null),
            MeasurementBoundary::FiniteNonzeroFloat,
        );
        pending_unchanged(
            &state(
                Value::Integer(PyInteger::new(false, vec![0, 0, 1])),
                Value::Bool(false),
            ),
            MeasurementBoundary::IntegerMagnitudeBeyond64Bits,
        );
        pending_unchanged(
            &state(text("other"), Value::Array(vec![Value::Array(vec![])])),
            MeasurementBoundary::ContainerBeyondChildLevel,
        );
        pending_unchanged(
            &state(
                text("self"),
                Value::Integer(PyInteger::new(true, vec![1, 0, 1])),
            ),
            MeasurementBoundary::IntegerMagnitudeBeyond64Bits,
        );
        let deferred = state(
            text(&"A".repeat(614401)),
            Value::Array(vec![Value::Object(PyObject::new(vec![]).unwrap())]),
        );
        pending_unchanged(&deferred, MeasurementBoundary::ContainerBeyondChildLevel);
    }

    #[test]
    fn sdp2_other_model_ignores_bounded_malformed_payload_without_decoding() {
        let malformed = map((0..129)
            .map(|i| (format!("bad-{i}").as_str().into(), text("not Base64!")))
            .collect());
        let mut cache = seed();
        let before = cache.clone();
        for model in [
            Value::Null,
            Value::Bool(false),
            Value::Integer(PyInteger::new(false, vec![])),
            Value::FloatBits(0),
            Value::FloatBits(0xfff8_0000_0000_0001),
            text("SELF"),
            text("other"),
            Value::Array(vec![Value::Null]),
            object(vec![]),
        ] {
            let input = state(model, malformed.clone());
            assert_eq!(
                restore_payload(&mut cache, &input, &identity()).unwrap(),
                RestoreOutcome::IgnoredOtherModel
            );
            assert_eq!(cache, before);
        }
        refusal_unchanged(&state(text("self"), malformed), CacheSite::VectorMap);
        for malformed in [
            Value::Null,
            Value::Bool(true),
            text("bad"),
            Value::Array(vec![]),
        ] {
            let input = state(text("other"), malformed.clone());
            assert_eq!(
                restore_payload(&mut cache, &input, &identity()).unwrap(),
                RestoreOutcome::IgnoredOtherModel
            );
            refusal_unchanged(&state(text("self"), malformed), CacheSite::VectorMap);
        }
        // SuppliedIdentity is a code-point string, with no hex/model attestation.
        let supplied = SuppliedIdentity::new(PyString::from_codepoints(vec![0xd800]).unwrap());
        assert_eq!(
            restore_payload(
                &mut cache,
                &state(Value::String(supplied.value().clone()), map(vec![])),
                &supplied
            )
            .unwrap(),
            RestoreOutcome::Replaced
        );
        assert!(cache.entries().is_empty());
    }

    #[test]
    fn sdp2_restore_entry_count_boundaries_and_order() {
        let mut cache = seed();
        assert_eq!(
            restore_payload(&mut cache, &state(text("self"), map(vec![])), &identity()).unwrap(),
            RestoreOutcome::Replaced
        );
        assert!(cache.entries().is_empty());
        assert_eq!(
            restore_payload(
                &mut cache,
                &state(text("self"), vector_map(128)),
                &identity()
            )
            .unwrap(),
            RestoreOutcome::Replaced
        );
        assert_eq!(cache.entries().len(), 128);
        for (index, entry) in cache.entries().iter().enumerate() {
            assert_eq!(entry.key(), &key(127 - index));
            assert_eq!(entry.bits().len(), 384);
            assert_eq!(entry.bits()[0], 0x3f80_0000);
            assert!(entry.bits()[1..].iter().all(|&bits| bits == 0));
        }
        refusal_unchanged(&state(text("self"), vector_map(129)), CacheSite::VectorMap);
        let first_invalid = map(vec![("bad".into(), Value::Null), (key(1), Value::Null)]);
        refusal_unchanged(&state(text("self"), first_invalid), CacheSite::EntryKey(0));
        let first_encoded = map(vec![(key(0), Value::Null), ("bad".into(), Value::Null)]);
        refusal_unchanged(
            &state(text("self"), first_encoded),
            CacheSite::EntryEncoded(0),
        );
    }

    #[test]
    fn sdp2_restore_literal_le_bits_finiteness_nonzero_and_atomic_failure() {
        let mut cache = seed();
        assert_eq!(cache.entries()[0].bits()[0], 0x3f80_0000);
        assert!(cache.entries()[0].bits()[1..].iter().all(|&bits| bits == 0));
        let subnormal = format!("AQAAAA{}", "A".repeat(2042));
        assert_eq!(
            restore_payload(
                &mut cache,
                &state(text("self"), map(vec![(key(1), text(&subnormal))])),
                &identity()
            )
            .unwrap(),
            RestoreOutcome::Replaced
        );
        assert_eq!(cache.entries()[0].bits()[0], 1);
        assert!(cache.entries()[0].bits()[1..].iter().all(|&bits| bits == 0));
        for prefix in ["AACAfw", "AACA/w", "AQDAfw", "AAAAgA", "AAAAAA"] {
            let encoded = format!("{prefix}{}", "A".repeat(2042));
            refusal_unchanged(
                &state(text("self"), map(vec![(key(0), text(&encoded))])),
                CacheSite::EntryVector(0),
            );
        }
        let later_invalid = map(vec![
            (key(4), text(&literal_one())),
            (key(5), text(&format!("AACAfw{}", "A".repeat(2042)))),
            ("bad".into(), Value::Null),
        ]);
        refusal_unchanged(
            &state(text("self"), later_invalid),
            CacheSite::EntryVector(1),
        );
    }

    #[test]
    fn sdp2_restore_key_value_ascii_encoding_and_raw_byte_guards() {
        for bad_key in [
            "a".repeat(63).as_str().into(),
            "a".repeat(65).as_str().into(),
            "A".repeat(64).as_str().into(),
            "g".repeat(64).as_str().into(),
            PyString::from_codepoints(vec![0x100; 64]).unwrap(),
        ] {
            refusal_unchanged(
                &state(text("self"), map(vec![(bad_key, text(&literal_one()))])),
                CacheSite::EntryKey(0),
            );
        }
        for bad in [
            Value::Null,
            text(""),
            text(&"A".repeat(2047)),
            text(&"A".repeat(2049)),
        ] {
            refusal_unchanged(
                &state(text("self"), map(vec![(key(0), bad)])),
                CacheSite::EntryEncoded(0),
            );
        }
        for first in [0x100, 0xd800, 0x20, 0x0a, 0x2d, 0x5f, 0x3d] {
            let mut cps = vec![0x41; 2048];
            cps[0] = first;
            refusal_unchanged(
                &state(
                    text("self"),
                    map(vec![(
                        key(0),
                        Value::String(PyString::from_codepoints(cps).unwrap()),
                    )]),
                ),
                CacheSite::EntryBase64(0),
            );
        }
        for bad in [
            format!("A={}A", "A".repeat(2045)),
            format!("{}===", "A".repeat(2045)),
        ] {
            refusal_unchanged(
                &state(text("self"), map(vec![(key(0), text(&bad))])),
                CacheSite::EntryBase64(0),
            );
        }
        for short_raw in [
            format!("{}====", "A".repeat(2044)),
            format!("{}==", "A".repeat(2046)),
            format!("{}=", "A".repeat(2047)),
        ] {
            refusal_unchanged(
                &state(text("self"), map(vec![(key(0), text(&short_raw))])),
                CacheSite::EntryBytes(0),
            );
        }
    }

    #[test]
    fn sdp2_snapshot_zero_arbitrary_keys_order_and_duplicate_ir() {
        let strange = PyString::from_codepoints(vec![0xd800, 0x1f642]).unwrap();
        let input = SnapshotEntries::new(vec![
            (strange.clone(), vec![0; 384]),
            ("".into(), vec![0; 384]),
        ])
        .unwrap();
        let output = snapshot_payload(&input, &identity()).unwrap();
        let Value::Object(state_object) = &output else {
            panic!("snapshot object")
        };
        let Some(Value::Object(vectors)) = state_object.get_str("vectors") else {
            panic!("snapshot vectors")
        };
        assert_eq!(vectors.entries()[0].0, strange);
        assert_eq!(vectors.entries()[1].0, PyString::from(""));
        assert_eq!(vectors.entries()[0].1, text(&"A".repeat(2048)));
        refusal_unchanged(&output, CacheSite::EntryKey(0));
        let zero = snapshot_payload(
            &SnapshotEntries::new(vec![(key(0), vec![0; 384])]).unwrap(),
            &identity(),
        )
        .unwrap();
        refusal_unchanged(&zero, CacheSite::EntryVector(0));
        assert_eq!(
            SnapshotEntries::new(vec![("same".into(), vec![]), ("same".into(), vec![0; 384])])
                .unwrap_err(),
            IrError::DuplicateObjectKey {
                first: 0,
                duplicate: 1
            }
        );
    }

    #[test]
    fn sdp2_snapshot_all_vectors_precede_count_and_size() {
        let entries: Vec<_> = (0..129).map(|i| (key(i), vec![0; 384])).collect();
        assert!(snapshot_payload(
            &SnapshotEntries::new(entries[..128].to_vec()).unwrap(),
            &identity()
        )
        .is_ok());
        assert_eq!(
            snapshot_payload(&SnapshotEntries::new(entries.clone()).unwrap(), &identity()),
            Err(refuse(CacheSite::SnapshotCount))
        );
        for bad in [
            vec![0; 383],
            vec![0; 385],
            vec![0x7f80_0000; 384],
            vec![0xffc0_0001; 384],
        ] {
            let mut later = entries.clone();
            later.push((key(129), bad));
            assert_eq!(
                snapshot_payload(&SnapshotEntries::new(later).unwrap(), &identity()),
                Err(refuse(CacheSite::SnapshotVector(129)))
            );
        }
        let huge_key: PyString = "a".repeat(614401).as_str().into();
        assert_eq!(
            snapshot_payload(
                &SnapshotEntries::new(vec![(huge_key.clone(), vec![0; 384])]).unwrap(),
                &identity()
            ),
            Err(refuse(CacheSite::EnvelopeSize))
        );
        let later = SnapshotEntries::new(vec![
            (huge_key, vec![0; 384]),
            ("later".into(), vec![0; 383]),
        ])
        .unwrap();
        assert_eq!(
            snapshot_payload(&later, &identity()),
            Err(refuse(CacheSite::SnapshotVector(1)))
        );
    }

    #[test]
    fn sdp2_snapshot_restore_preserves_finite_signed_bits_and_literal_encoding() {
        let mut one = vec![0; 384];
        one[0] = 0x3f80_0000;
        let mut varied = vec![0; 384];
        varied[..8].copy_from_slice(&[
            0,
            0x8000_0000,
            1,
            0x8000_0001,
            0x007f_ffff,
            0x807f_ffff,
            0x7f7f_ffff,
            0xff7f_ffff,
        ]);
        let input =
            SnapshotEntries::new(vec![(key(9), one.clone()), (key(3), varied.clone())]).unwrap();
        let output = snapshot_payload(&input, &identity()).unwrap();
        let Value::Object(object) = &output else {
            panic!("fixture object")
        };
        let Some(Value::Object(vectors)) = object.get_str("vectors") else {
            panic!("fixture vectors")
        };
        assert_eq!(vectors.entries()[0], (key(9), text(&literal_one())));
        let mut cache = seed();
        assert_eq!(
            restore_payload(&mut cache, &output, &identity()).unwrap(),
            RestoreOutcome::Replaced
        );
        assert_eq!(cache.entries()[0].key(), &key(9));
        assert_eq!(cache.entries()[0].bits(), one.as_slice());
        assert_eq!(cache.entries()[1].key(), &key(3));
        assert_eq!(cache.entries()[1].bits(), varied.as_slice());
    }

    #[test]
    fn sdp2_stream_admission_integrates_fresh_restore_and_absence() {
        let request = object(vec![
            ("query", text("query")),
            (
                "notes",
                Value::Array(vec![object(vec![
                    ("body", text("body")),
                    (
                        "body_sha256",
                        text("230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5"),
                    ),
                ])]),
            ),
        ]);
        let mut cache = seed();
        let before = cache.clone();
        for phase in [StreamPhase::Fresh, StreamPhase::Active] {
            let input = object(vec![("id", text("sdp2")), ("request", request.clone())]);
            let admitted =
                validate_stream_hashes(&input, CachePolicy::AdvertisedCache, phase).unwrap();
            assert_eq!(
                restore_stream_cache(&mut cache, &admitted, &identity()).unwrap(),
                RestoreOutcome::NoOp
            );
            assert_eq!(cache, before);
        }
        for payload in [
            Value::Null,
            state(text("other"), Value::Bool(false)),
            state(text("self"), vector_map(0)),
        ] {
            let input = object(vec![
                ("id", text("sdp2")),
                ("request", request.clone()),
                ("cache", payload.clone()),
            ]);
            let admitted =
                validate_stream_hashes(&input, CachePolicy::AdvertisedCache, StreamPhase::Fresh)
                    .unwrap();
            let expected = match &payload {
                Value::Null => RestoreOutcome::NoOp,
                Value::Object(object) if object.get_str("model_sha256") == Some(&text("other")) => {
                    RestoreOutcome::IgnoredOtherModel
                }
                _ => RestoreOutcome::Replaced,
            };
            assert_eq!(
                restore_stream_cache(&mut cache, &admitted, &identity()).unwrap(),
                expected
            );
            if expected != RestoreOutcome::Replaced {
                assert_eq!(cache, before);
            }
        }
        assert!(cache.entries().is_empty());
    }
}
