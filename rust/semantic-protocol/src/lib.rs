//! Already-decoded semantic/4 prefixes, explicit remaining work and unqualified
//! logical projections. There is no decoder, serializer or initialized backend.
//!
//! `Value` equality compares IR representations, including float bits and value
//! tags. It is not Python numeric equality. Hash and cache comparisons implement
//! their selected Python inequality rules separately.

use cairn_semantic_text::is_python_blank;

pub mod cache;
pub mod hash;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum IrError {
    InvalidCodepoint { index: usize, codepoint: u32 },
    DuplicateObjectKey { first: usize, duplicate: usize },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Utf8Error {
    pub index: usize,
    pub codepoint: u32,
}

/// Ordered Python code points, including lone surrogates. No normalization or
/// surrogate-pair combination is performed.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PyString(Vec<u32>);

impl PyString {
    pub fn from_codepoints(codepoints: Vec<u32>) -> Result<Self, IrError> {
        for (index, &codepoint) in codepoints.iter().enumerate() {
            if codepoint > 0x10ffff {
                return Err(IrError::InvalidCodepoint { index, codepoint });
            }
        }
        Ok(Self(codepoints))
    }

    pub fn codepoints(&self) -> &[u32] {
        &self.0
    }

    pub fn len(&self) -> usize {
        self.0.len()
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub fn strict_utf8(&self) -> Result<Vec<u8>, Utf8Error> {
        let mut bytes = Vec::new();
        for (index, &codepoint) in self.0.iter().enumerate() {
            let scalar = char::from_u32(codepoint).ok_or(Utf8Error { index, codepoint })?;
            let mut encoded = [0; 4];
            bytes.extend_from_slice(scalar.encode_utf8(&mut encoded).as_bytes());
        }
        Ok(bytes)
    }

    pub fn strict_utf8_len(&self) -> Result<usize, Utf8Error> {
        let mut length = 0;
        for (index, &codepoint) in self.0.iter().enumerate() {
            let scalar = char::from_u32(codepoint).ok_or(Utf8Error { index, codepoint })?;
            length += scalar.len_utf8();
        }
        Ok(length)
    }

    fn equals_str(&self, text: &str) -> bool {
        self.0.iter().copied().eq(text.chars().map(u32::from))
    }

    fn python_blank(&self) -> bool {
        self.0
            .iter()
            .all(|&codepoint| match char::from_u32(codepoint) {
                Some(scalar) => {
                    let mut encoded = [0; 4];
                    is_python_blank(scalar.encode_utf8(&mut encoded))
                }
                None => false,
            })
    }
}

impl From<&str> for PyString {
    fn from(text: &str) -> Self {
        Self(text.chars().map(u32::from).collect())
    }
}

/// Canonical sign and little-endian u32 magnitude limbs, with no precision cap.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PyInteger {
    negative: bool,
    magnitude: Vec<u32>,
}

impl PyInteger {
    pub fn new(negative: bool, mut magnitude: Vec<u32>) -> Self {
        while magnitude.last() == Some(&0) {
            magnitude.pop();
        }
        Self {
            negative: negative && !magnitude.is_empty(),
            magnitude,
        }
    }

    pub fn is_negative(&self) -> bool {
        self.negative
    }

    pub fn magnitude(&self) -> &[u32] {
        &self.magnitude
    }
}

/// FloatBits retains every binary64 bit. Structural equality here is not
/// Python comparison: Bool, Integer and FloatBits remain distinct IR variants.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    Null,
    Bool(bool),
    Integer(PyInteger),
    FloatBits(u64),
    String(PyString),
    Array(Vec<Value>),
    Object(PyObject),
}

/// Unique decoded keys in insertion order. Duplicate rejection is an IR
/// invariant, not a disposition for a raw duplicate-key JSON frame.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PyObject(Vec<(PyString, Value)>);

impl PyObject {
    pub fn new(entries: Vec<(PyString, Value)>) -> Result<Self, IrError> {
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

    pub fn entries(&self) -> &[(PyString, Value)] {
        &self.0
    }

    pub fn get(&self, key: &PyString) -> Option<&Value> {
        self.0
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
    }

    fn get_str(&self, key: &str) -> Option<&Value> {
        self.0
            .iter()
            .find(|(name, _)| name.equals_str(key))
            .map(|(_, value)| value)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FailureKind {
    KeyError,
    TypeError,
    AttributeError,
    UnicodeEncodeError,
    ValueError,
}

/// Internal operation diagnostics, not wire names or traceback wording.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Site {
    RankNotesLookup,
    RankQueryLookup,
    RankNotesLength,
    RankNoteCount,
    RankQueryStrip,
    RankQueryEncode,
    RankQueryGuard,
    RankBodyLookup(usize),
    RankBodyEncode(usize),
    RankBodyTotal,
    RankExpectedHashLookup(usize),
    RankBodyHashMismatch(usize),
    StreamKeySet,
    StreamKeys,
    StreamIdLookup,
    StreamIdGuard,
    StreamRequestLookup,
    StreamLateCache,
    EmbedKeys,
    EmbedIdLookup,
    EmbedOperationLookup,
    EmbedTextLookup,
    EmbedIdGuard,
    EmbedTextGuard,
    EmbedOperation,
    EmbedIdentityText,
    EmbedQueryGuard,
    EmbedQueryEncode,
    EmbedDocumentGuard,
    EmbedDocumentEncode,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Refusal {
    pub site: Site,
    pub kind: FailureKind,
}

fn refusal(site: Site, kind: FailureKind) -> Refusal {
    Refusal { site, kind }
}

fn lookup<'a>(input: &'a Value, key: &str, site: Site) -> Result<&'a Value, Refusal> {
    match input {
        Value::Object(object) => object
            .get_str(key)
            .ok_or(refusal(site, FailureKind::KeyError)),
        _ => Err(refusal(site, FailureKind::TypeError)),
    }
}

fn python_len(value: &Value, site: Site) -> Result<usize, Refusal> {
    match value {
        Value::String(text) => Ok(text.len()),
        Value::Array(values) => Ok(values.len()),
        Value::Object(object) => Ok(object.entries().len()),
        _ => Err(refusal(site, FailureKind::TypeError)),
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RemainingRankBoundary {
    HashBodyThenLookupExpectedThenPythonInequality,
}

/// Stops before hashes. For each note, `hash::admit_rank_hashes` hashes body_i, looks
/// up body_sha256_i (missing is a lookup failure), then compares with Python !=.
/// A non-equal Value, including a non-string, is a mismatch, not a type refusal.
/// The first failing note wins. This type has no completion/readiness method.
#[derive(Debug)]
pub struct RankPendingHashes<'a> {
    input: &'a Value,
    notes: &'a [Value],
    query: &'a PyString,
    query_utf8: Vec<u8>,
    body_utf8: Vec<Vec<u8>>,
}

impl<'a> RankPendingHashes<'a> {
    pub fn input(&self) -> &'a Value {
        self.input
    }
    pub fn notes(&self) -> &'a [Value] {
        self.notes
    }
    pub fn query(&self) -> &'a PyString {
        self.query
    }
    pub fn query_utf8(&self) -> &[u8] {
        &self.query_utf8
    }
    pub fn body_utf8(&self) -> &[Vec<u8>] {
        &self.body_utf8
    }
    pub fn remaining_boundary(&self) -> RemainingRankBoundary {
        RemainingRankBoundary::HashBodyThenLookupExpectedThenPythonInequality
    }
}

pub fn validate_rank_prefix(input: &Value) -> Result<RankPendingHashes<'_>, Refusal> {
    let notes_value = lookup(input, "notes", Site::RankNotesLookup)?;
    let query_value = lookup(input, "query", Site::RankQueryLookup)?;
    let count = python_len(notes_value, Site::RankNotesLength)?;
    if !(1..=64).contains(&count) {
        return Err(refusal(Site::RankNoteCount, FailureKind::ValueError));
    }
    let query = match query_value {
        Value::String(query) => query,
        _ => return Err(refusal(Site::RankQueryStrip, FailureKind::AttributeError)),
    };
    if query.python_blank() {
        return Err(refusal(Site::RankQueryGuard, FailureKind::ValueError));
    }
    let query_utf8 = query
        .strict_utf8()
        .map_err(|_| refusal(Site::RankQueryEncode, FailureKind::UnicodeEncodeError))?;
    if query_utf8.len() > 4096 {
        return Err(refusal(Site::RankQueryGuard, FailureKind::ValueError));
    }
    // Objects iterate keys and strings iterate characters. At this point each
    // is nonempty; its first element fails the following ["body"] operation.
    let notes = match notes_value {
        Value::Array(notes) => notes.as_slice(),
        _ => return Err(refusal(Site::RankBodyLookup(0), FailureKind::TypeError)),
    };
    let mut body_utf8 = Vec::with_capacity(count);
    let mut total = 0usize;
    for (index, note) in notes.iter().enumerate() {
        let body = lookup(note, "body", Site::RankBodyLookup(index))?;
        let body = match body {
            Value::String(body) => body,
            _ => {
                return Err(refusal(
                    Site::RankBodyEncode(index),
                    FailureKind::AttributeError,
                ))
            }
        };
        let bytes = body
            .strict_utf8()
            .map_err(|_| refusal(Site::RankBodyEncode(index), FailureKind::UnicodeEncodeError))?;
        // Only the final bound matters; do not reject before later encodings.
        total = total.saturating_add(bytes.len());
        body_utf8.push(bytes);
    }
    if total > 1024 * 1024 {
        return Err(refusal(Site::RankBodyTotal, FailureKind::ValueError));
    }
    Ok(RankPendingHashes {
        input,
        notes,
        query,
        query_utf8,
        body_utf8,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CachePolicy {
    NoCache,
    AdvertisedCache,
}

#[derive(Default)]
struct KeySet {
    id: bool,
    request: bool,
    cache: bool,
    operation: bool,
    text: bool,
    other: bool,
}

impl KeySet {
    fn observe(&mut self, name: &PyString) {
        if name.equals_str("id") {
            self.id = true;
        } else if name.equals_str("request") {
            self.request = true;
        } else if name.equals_str("cache") {
            self.cache = true;
        } else if name.equals_str("operation") {
            self.operation = true;
        } else if name.equals_str("text") {
            self.text = true;
        } else {
            self.other = true;
        }
    }
}

fn key_set(input: &Value, site: Site) -> Result<KeySet, Refusal> {
    let mut keys = KeySet::default();
    match input {
        Value::Object(object) => {
            for (key, _) in object.entries() {
                keys.observe(key);
            }
        }
        Value::Array(values) => {
            for value in values {
                match value {
                    Value::Array(_) | Value::Object(_) => {
                        return Err(refusal(site, FailureKind::TypeError))
                    }
                    Value::String(key) => keys.observe(key),
                    _ => keys.other = true,
                }
            }
        }
        Value::String(text) => {
            // Iteration yields one-character keys; every allowed name is longer.
            keys.other = !text.is_empty();
        }
        _ => return Err(refusal(site, FailureKind::TypeError)),
    }
    Ok(keys)
}

#[derive(Debug)]
pub struct StreamPendingHashes<'a> {
    input: &'a Value,
    id: &'a PyString,
    rank: RankPendingHashes<'a>,
    cache: Option<&'a Value>,
    policy: CachePolicy,
}

impl<'a> StreamPendingHashes<'a> {
    pub fn input(&self) -> &'a Value {
        self.input
    }
    pub fn id(&self) -> &'a PyString {
        self.id
    }
    pub fn rank(&self) -> &RankPendingHashes<'a> {
        &self.rank
    }
    pub fn cache(&self) -> Option<&'a Value> {
        self.cache
    }
    pub fn policy(&self) -> CachePolicy {
        self.policy
    }

    /// Pure logical wrapping only. Hashes, scoring, serialization and reply byte
    /// bounds are not established. Advertised-cache replies remain unavailable.
    pub fn unqualified_nocache_projection<'b>(
        &'b self,
        result: UncheckedLogicalResult<'b>,
    ) -> Result<UnqualifiedStreamEnvelope<'b>, ProjectionFailure> {
        if self.policy != CachePolicy::NoCache {
            return Err(ProjectionFailure::CacheReplyUnavailable);
        }
        Ok(UnqualifiedStreamEnvelope {
            id: self.id,
            result,
        })
    }
}

pub fn validate_stream_prefix(
    input: &Value,
    policy: CachePolicy,
) -> Result<StreamPendingHashes<'_>, Refusal> {
    let keys = key_set(input, Site::StreamKeySet)?;
    if !keys.id
        || !keys.request
        || keys.other
        || keys.operation
        || keys.text
        || (keys.cache && policy == CachePolicy::NoCache)
    {
        return Err(refusal(Site::StreamKeys, FailureKind::ValueError));
    }
    let object = match input {
        Value::Object(object) => object,
        _ => return Err(refusal(Site::StreamIdLookup, FailureKind::TypeError)),
    };
    let id_value = object
        .get_str("id")
        .ok_or(refusal(Site::StreamIdLookup, FailureKind::KeyError))?;
    let id = match id_value {
        Value::String(id) if (1..=64).contains(&id.len()) => id,
        _ => return Err(refusal(Site::StreamIdGuard, FailureKind::ValueError)),
    };
    let request = object
        .get_str("request")
        .ok_or(refusal(Site::StreamRequestLookup, FailureKind::KeyError))?;
    let rank = validate_rank_prefix(request)?;
    let cache = object.get_str("cache");
    Ok(StreamPendingHashes {
        input,
        id,
        rank,
        cache,
        policy,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TokenizerOperation {
    Query,
    Document,
}

#[derive(Debug)]
pub struct NeedsInitializedIdentity<'a> {
    input: &'a Value,
    id: &'a PyString,
}

impl<'a> NeedsInitializedIdentity<'a> {
    pub fn input(&self) -> &'a Value {
        self.input
    }
    pub fn id(&self) -> &'a PyString {
        self.id
    }
}

#[derive(Debug)]
pub struct NeedsTokenizer<'a> {
    input: &'a Value,
    id: &'a PyString,
    text: &'a PyString,
    operation: TokenizerOperation,
    utf8: Vec<u8>,
}

impl<'a> NeedsTokenizer<'a> {
    pub fn input(&self) -> &'a Value {
        self.input
    }
    pub fn id(&self) -> &'a PyString {
        self.id
    }
    pub fn text(&self) -> &'a PyString {
        self.text
    }
    pub fn operation(&self) -> TokenizerOperation {
        self.operation
    }
    pub fn utf8(&self) -> &[u8] {
        &self.utf8
    }
}

#[derive(Debug)]
pub enum EmbedPrefix<'a> {
    NeedsInitializedIdentity(NeedsInitializedIdentity<'a>),
    NeedsTokenizer(NeedsTokenizer<'a>),
}

/// Computes only the supplied decoded prefix. Actual Source constructs its
/// embeddings backend BEFORE reading input; no such constructor exists here.
pub fn validate_embed_prefix(input: &Value) -> Result<EmbedPrefix<'_>, Refusal> {
    if !matches!(input, Value::Object(_)) {
        return Err(refusal(Site::EmbedKeys, FailureKind::ValueError));
    }
    let keys = key_set(input, Site::EmbedKeys)?;
    if !keys.id || !keys.operation || !keys.text || keys.request || keys.cache || keys.other {
        return Err(refusal(Site::EmbedKeys, FailureKind::ValueError));
    }
    let id_value = lookup(input, "id", Site::EmbedIdLookup)?;
    let operation = lookup(input, "operation", Site::EmbedOperationLookup)?;
    let text_value = lookup(input, "text", Site::EmbedTextLookup)?;
    let id = match id_value {
        Value::String(id) if (1..=128).contains(&id.len()) => id,
        _ => return Err(refusal(Site::EmbedIdGuard, FailureKind::ValueError)),
    };
    let text = match text_value {
        Value::String(text) => text,
        _ => return Err(refusal(Site::EmbedTextGuard, FailureKind::ValueError)),
    };
    match operation {
        Value::String(operation) if operation.equals_str("identity") => {
            if !text.is_empty() {
                return Err(refusal(Site::EmbedIdentityText, FailureKind::ValueError));
            }
            Ok(EmbedPrefix::NeedsInitializedIdentity(
                NeedsInitializedIdentity { input, id },
            ))
        }
        Value::String(operation) if operation.equals_str("query") => {
            tokenizer_prefix(input, id, text, TokenizerOperation::Query)
        }
        Value::String(operation) if operation.equals_str("document") => {
            tokenizer_prefix(input, id, text, TokenizerOperation::Document)
        }
        _ => Err(refusal(Site::EmbedOperation, FailureKind::ValueError)),
    }
}

fn tokenizer_prefix<'a>(
    input: &'a Value,
    id: &'a PyString,
    text: &'a PyString,
    operation: TokenizerOperation,
) -> Result<EmbedPrefix<'a>, Refusal> {
    let (limit, guard, encode) = match operation {
        TokenizerOperation::Query => (4096, Site::EmbedQueryGuard, Site::EmbedQueryEncode),
        TokenizerOperation::Document => {
            (65536, Site::EmbedDocumentGuard, Site::EmbedDocumentEncode)
        }
    };
    if text.python_blank() {
        return Err(refusal(guard, FailureKind::ValueError));
    }
    let utf8 = text
        .strict_utf8()
        .map_err(|_| refusal(encode, FailureKind::UnicodeEncodeError))?;
    if utf8.len() > limit {
        return Err(refusal(guard, FailureKind::ValueError));
    }
    Ok(EmbedPrefix::NeedsTokenizer(NeedsTokenizer {
        input,
        id,
        text,
        operation,
        utf8,
    }))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EchoField {
    RecordId,
    Version,
    BodySha256,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProjectionFailure {
    EchoLookup {
        note_index: usize,
        field: EchoField,
        kind: FailureKind,
    },
    CacheReplyUnavailable,
}

#[derive(Debug)]
pub struct UncheckedNoteEcho<'a> {
    record_id: &'a Value,
    version: &'a Value,
    body_sha256: &'a Value,
}

impl<'a> UncheckedNoteEcho<'a> {
    pub fn record_id(&self) -> &'a Value {
        self.record_id
    }
    pub fn version(&self) -> &'a Value {
        self.version
    }
    pub fn body_sha256(&self) -> &'a Value {
        self.body_sha256
    }
}

#[derive(Debug)]
pub struct UnqualifiedRankEcho<'a> {
    notes: Vec<UncheckedNoteEcho<'a>>,
}

impl<'a> UnqualifiedRankEcho<'a> {
    pub fn notes(&self) -> &[UncheckedNoteEcho<'a>] {
        &self.notes
    }
}

fn echo_field<'a>(
    note: &'a Value,
    note_index: usize,
    key: &str,
    field: EchoField,
) -> Result<&'a Value, ProjectionFailure> {
    match note {
        Value::Object(object) => object.get_str(key).ok_or(ProjectionFailure::EchoLookup {
            note_index,
            field,
            kind: FailureKind::KeyError,
        }),
        _ => Err(ProjectionFailure::EchoLookup {
            note_index,
            field,
            kind: FailureKind::TypeError,
        }),
    }
}

/// Independent post-score metadata projection, NOT rank admission or a whole
/// worker's first-refusal ordering. Nonfinite version bits remain IR only:
/// actual Source json.dumps(allow_nan=False) would later refuse serialization.
pub fn project_unqualified_rank_echo(
    notes: &[Value],
) -> Result<UnqualifiedRankEcho<'_>, ProjectionFailure> {
    let mut echoes = Vec::with_capacity(notes.len());
    for (note_index, note) in notes.iter().enumerate() {
        let record_id = echo_field(note, note_index, "record_id", EchoField::RecordId)?;
        let version = echo_field(note, note_index, "version", EchoField::Version)?;
        let body_sha256 = echo_field(note, note_index, "body_sha256", EchoField::BodySha256)?;
        echoes.push(UncheckedNoteEcho {
            record_id,
            version,
            body_sha256,
        });
    }
    Ok(UnqualifiedRankEcho { notes: echoes })
}

#[derive(Debug)]
pub struct UncheckedLogicalResult<'a> {
    value: &'a Value,
}

impl<'a> UncheckedLogicalResult<'a> {
    pub fn new(value: &'a Value) -> Self {
        Self { value }
    }
    pub fn value(&self) -> &'a Value {
        self.value
    }
}

#[derive(Debug)]
pub struct UnqualifiedStreamEnvelope<'a> {
    id: &'a PyString,
    result: UncheckedLogicalResult<'a>,
}

impl<'a> UnqualifiedStreamEnvelope<'a> {
    pub fn id(&self) -> &'a PyString {
        self.id
    }
    pub fn result(&self) -> &UncheckedLogicalResult<'a> {
        &self.result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(value: &str) -> Value {
        Value::String(value.into())
    }

    fn object(entries: Vec<(&str, Value)>) -> Value {
        Value::Object(
            PyObject::new(
                entries
                    .into_iter()
                    .map(|(key, value)| (key.into(), value))
                    .collect(),
            )
            .unwrap(),
        )
    }

    fn body_note(body: Value) -> Value {
        object(vec![("body", body)])
    }

    fn rank(notes: Value, query: Value) -> Value {
        object(vec![("notes", notes), ("query", query)])
    }

    // Exact literal protocol-rank input from the bound reference inventory.
    fn protocol_rank() -> Value {
        object(vec![
            ("id", text("protocol-rank")),
            ("query", text("query")),
            (
                "notes",
                Value::Array(vec![object(vec![
                    ("record_id", text("fixture")),
                    ("version", Value::Integer(PyInteger::new(false, vec![1]))),
                    ("body", text("body")),
                    (
                        "body_sha256",
                        text("230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5"),
                    ),
                ])]),
            ),
        ])
    }

    fn stream(id: PyString, request: Value, cache: Option<Value>) -> Value {
        let mut entries = vec![("id", Value::String(id)), ("request", request)];
        if let Some(cache) = cache {
            entries.push(("cache", cache));
        }
        object(entries)
    }

    fn embed(id: PyString, operation: Value, body: Value) -> Value {
        object(vec![
            ("id", Value::String(id)),
            ("operation", operation),
            ("text", body),
        ])
    }

    fn surrogate() -> PyString {
        PyString::from_codepoints(vec![0xd800]).unwrap()
    }

    fn assert_refusal<T>(result: Result<T, Refusal>, site: Site, kind: FailureKind) {
        assert_eq!(result.err(), Some(Refusal { site, kind }));
    }

    #[test]
    fn lossless_ir_preserves_codepoints_limbs_float_bits_and_object_order() {
        // SDP-IR: these are IR/source-derived cases, not raw decoder oracles.
        assert_eq!(
            PyString::from_codepoints(vec![0x110000]).unwrap_err(),
            IrError::InvalidCodepoint {
                index: 0,
                codepoint: 0x110000
            }
        );
        let highest = PyString::from_codepoints(vec![0x10ffff]).unwrap();
        assert_eq!(
            highest.strict_utf8().unwrap().as_slice(),
            &[0xf4, 0x8f, 0xbf, 0xbf]
        );
        let pair = PyString::from_codepoints(vec![0xd83d, 0xde42]).unwrap();
        assert_eq!(pair.len(), 2);
        assert_eq!(pair.codepoints(), &[0xd83d, 0xde42]);
        assert_eq!(
            pair.strict_utf8().unwrap_err(),
            Utf8Error {
                index: 0,
                codepoint: 0xd83d
            }
        );
        assert_eq!(
            pair.strict_utf8_len().unwrap_err(),
            Utf8Error {
                index: 0,
                codepoint: 0xd83d
            }
        );
        let scalar = PyString::from("🙂e\u{0301}");
        assert_eq!(scalar.len(), 3);
        assert_eq!(
            scalar.strict_utf8().unwrap().as_slice(),
            "🙂e\u{0301}".as_bytes()
        );
        assert_eq!(scalar.strict_utf8_len().unwrap(), 7);
        let zero = PyInteger::new(true, vec![0, 0]);
        assert!(!zero.is_negative());
        assert!(zero.magnitude().is_empty());
        let negative = PyInteger::new(true, vec![7, 0, 0]);
        assert!(negative.is_negative());
        assert_eq!(negative.magnitude(), &[7]);
        let huge = PyInteger::new(false, vec![u32::MAX; 256]);
        assert_eq!(huge.magnitude(), vec![u32::MAX; 256].as_slice());
        assert_ne!(Value::Bool(false), Value::Integer(zero));
        let key = surrogate();
        let retained = PyObject::new(vec![
            (key.clone(), Value::Null),
            ("second".into(), Value::Integer(huge)),
        ])
        .unwrap();
        assert_eq!(retained.entries()[0].0, key);
        assert_eq!(retained.get(&surrogate()), Some(&Value::Null));
        assert_eq!(retained.get(&"missing".into()), None);
        assert_eq!(
            PyObject::new(vec![
                (surrogate(), Value::Null),
                (surrogate(), Value::Bool(true))
            ])
            .unwrap_err(),
            IrError::DuplicateObjectKey {
                first: 0,
                duplicate: 1
            }
        );
    }

    #[test]
    fn rank_lookup_short_circuit_and_type_at_use_follow_source() {
        // SDP-RANK-ORDER and R-1: tuple lookups precede the len/strip condition.
        let missing = object(vec![]);
        assert_refusal(
            validate_rank_prefix(&missing),
            Site::RankNotesLookup,
            FailureKind::KeyError,
        );
        let missing_query = object(vec![("notes", Value::Null)]);
        assert_refusal(
            validate_rank_prefix(&missing_query),
            Site::RankQueryLookup,
            FailureKind::KeyError,
        );
        let null_notes = rank(Value::Null, text("query"));
        assert_refusal(
            validate_rank_prefix(&null_notes),
            Site::RankNotesLength,
            FailureKind::TypeError,
        );
        let empty_notes = rank(Value::Array(vec![]), Value::Null);
        assert_refusal(
            validate_rank_prefix(&empty_notes),
            Site::RankNoteCount,
            FailureKind::ValueError,
        );
        for notes in [object(vec![("key", Value::Null)]), text("x")] {
            let input = rank(notes.clone(), Value::Null);
            assert_refusal(
                validate_rank_prefix(&input),
                Site::RankQueryStrip,
                FailureKind::AttributeError,
            );
            let input = rank(notes, text("query"));
            assert_refusal(
                validate_rank_prefix(&input),
                Site::RankBodyLookup(0),
                FailureKind::TypeError,
            );
        }
        let missing_body = rank(Value::Array(vec![object(vec![])]), text("query"));
        assert_refusal(
            validate_rank_prefix(&missing_body),
            Site::RankBodyLookup(0),
            FailureKind::KeyError,
        );
        let null_body = rank(Value::Array(vec![body_note(Value::Null)]), text("query"));
        assert_refusal(
            validate_rank_prefix(&null_body),
            Site::RankBodyEncode(0),
            FailureKind::AttributeError,
        );
        let bad_query = rank(
            Value::Array(vec![object(vec![])]),
            Value::String(surrogate()),
        );
        assert_refusal(
            validate_rank_prefix(&bad_query),
            Site::RankQueryEncode,
            FailureKind::UnicodeEncodeError,
        );
    }

    #[test]
    fn rank_encodes_every_body_before_total_limit_and_retains_hash_stop() {
        let input = protocol_rank();
        let pending = validate_rank_prefix(&input).unwrap();
        assert_eq!(pending.input(), &input);
        assert_eq!(pending.query_utf8(), b"query");
        assert_eq!(pending.body_utf8(), &[b"body".to_vec()]);
        assert_eq!(
            pending.remaining_boundary(),
            RemainingRankBoundary::HashBodyThenLookupExpectedThenPythonInequality
        );
        // SDP-HASH-STOP: absent/Null hash and missing echo metadata stay pending.
        let input = rank(
            Value::Array(vec![
                body_note(text("one")),
                object(vec![("body", text("two")), ("body_sha256", Value::Null)]),
            ]),
            text("query"),
        );
        let pending = validate_rank_prefix(&input).unwrap();
        assert_eq!(pending.body_utf8(), &[b"one".to_vec(), b"two".to_vec()]);
        let maximum = rank(
            Value::Array(vec![
                body_note(text(&"a".repeat(524_288))),
                body_note(text(&"b".repeat(524_288))),
            ]),
            text("query"),
        );
        let pending = validate_rank_prefix(&maximum).unwrap();
        assert_eq!(pending.body_utf8().len(), 2);
        assert_eq!(pending.body_utf8()[0].len(), 524_288);
        assert_eq!(pending.body_utf8()[1].len(), 524_288);
        let overflow = rank(
            Value::Array(vec![
                body_note(text(&"a".repeat(524_288))),
                body_note(text(&"b".repeat(524_289))),
            ]),
            text("query"),
        );
        assert_refusal(
            validate_rank_prefix(&overflow),
            Site::RankBodyTotal,
            FailureKind::ValueError,
        );
        let later_encoding = rank(
            Value::Array(vec![
                body_note(text(&"a".repeat(1_048_577))),
                body_note(Value::String(surrogate())),
            ]),
            text("query"),
        );
        assert_refusal(
            validate_rank_prefix(&later_encoding),
            Site::RankBodyEncode(1),
            FailureKind::UnicodeEncodeError,
        );
    }

    #[test]
    fn rank_note_count_query_bytes_and_extra_keys_remain_permissive() {
        // rank/64-short reuses ONLY its count boundary; bodies are SDP literals.
        let sixty_four = rank(
            Value::Array(vec![body_note(text("body")); 64]),
            text("query"),
        );
        assert_eq!(validate_rank_prefix(&sixty_four).unwrap().notes().len(), 64);
        let sixty_five = rank(
            Value::Array(vec![body_note(text("body")); 65]),
            text("query"),
        );
        assert_refusal(
            validate_rank_prefix(&sixty_five),
            Site::RankNoteCount,
            FailureKind::ValueError,
        );
        let unicode = "漢".repeat(1365) + "a";
        let maximum = rank(Value::Array(vec![body_note(text("body"))]), text(&unicode));
        assert_eq!(
            validate_rank_prefix(&maximum).unwrap().query_utf8().len(),
            4096
        );
        let overflow = rank(
            Value::Array(vec![body_note(text("body"))]),
            text(&(unicode + "a")),
        );
        assert_refusal(
            validate_rank_prefix(&overflow),
            Site::RankQueryGuard,
            FailureKind::ValueError,
        );
        let blank = rank(
            Value::Array(vec![body_note(text("body"))]),
            text("\u{001c}"),
        );
        assert_refusal(
            validate_rank_prefix(&blank),
            Site::RankQueryGuard,
            FailureKind::ValueError,
        );
        let extra = object(vec![
            (
                "notes",
                Value::Array(vec![object(vec![
                    ("body", text("body")),
                    ("extra", Value::Bool(false)),
                ])]),
            ),
            ("query", text("query")),
            ("extra", Value::Null),
        ]);
        assert_eq!(validate_rank_prefix(&extra).unwrap().input(), &extra);
    }

    #[test]
    fn stream_key_set_type_at_use_and_id_order_precede_rank() {
        // R-1/SDP-STREAM-SET: array key names pass set(), then fail ["id"].
        let array = Value::Array(vec![text("id"), text("request")]);
        assert_refusal(
            validate_stream_prefix(&array, CachePolicy::NoCache),
            Site::StreamIdLookup,
            FailureKind::TypeError,
        );
        let unhashable = Value::Array(vec![
            text("extra"),
            object(vec![]),
            text("id"),
            text("request"),
        ]);
        assert_refusal(
            validate_stream_prefix(&unhashable, CachePolicy::NoCache),
            Site::StreamKeySet,
            FailureKind::TypeError,
        );
        assert_refusal(
            validate_stream_prefix(&Value::Null, CachePolicy::NoCache),
            Site::StreamKeySet,
            FailureKind::TypeError,
        );
        let extra = object(vec![
            ("id", Value::Null),
            ("request", Value::Null),
            ("extra", Value::Null),
        ]);
        assert_refusal(
            validate_stream_prefix(&extra, CachePolicy::NoCache),
            Site::StreamKeys,
            FailureKind::ValueError,
        );
        let missing = object(vec![("id", Value::Null)]);
        assert_refusal(
            validate_stream_prefix(&missing, CachePolicy::NoCache),
            Site::StreamKeys,
            FailureKind::ValueError,
        );
        let bad_id = object(vec![("id", Value::Null), ("request", Value::Null)]);
        assert_refusal(
            validate_stream_prefix(&bad_id, CachePolicy::NoCache),
            Site::StreamIdGuard,
            FailureKind::ValueError,
        );
        let bad_rank = stream("id".into(), Value::Null, Some(Value::Null));
        assert_refusal(
            validate_stream_prefix(&bad_rank, CachePolicy::AdvertisedCache),
            Site::RankNotesLookup,
            FailureKind::TypeError,
        );
        let surrogate_key = Value::Object(
            PyObject::new(vec![
                ("id".into(), Value::Null),
                ("request".into(), Value::Null),
                (surrogate(), Value::Null),
            ])
            .unwrap(),
        );
        assert_refusal(
            validate_stream_prefix(&surrogate_key, CachePolicy::NoCache),
            Site::StreamKeys,
            FailureKind::ValueError,
        );
    }

    #[test]
    fn stream_cache_presence_is_retained_without_restore_or_identity_checks() {
        for cache in [None, Some(Value::Null), Some(Value::Bool(false))] {
            let input = stream("id".into(), protocol_rank(), cache.clone());
            let pending = validate_stream_prefix(&input, CachePolicy::AdvertisedCache).unwrap();
            assert_eq!(pending.input(), &input);
            assert_eq!(pending.cache(), cache.as_ref());
            assert_eq!(pending.rank().body_utf8(), &[b"body".to_vec()]);
            let result = Value::Null;
            assert_eq!(
                pending
                    .unqualified_nocache_projection(UncheckedLogicalResult::new(&result))
                    .unwrap_err(),
                ProjectionFailure::CacheReplyUnavailable
            );
        }
        let input = stream("id".into(), protocol_rank(), Some(Value::Null));
        assert_refusal(
            validate_stream_prefix(&input, CachePolicy::NoCache),
            Site::StreamKeys,
            FailureKind::ValueError,
        );
    }

    #[test]
    fn id_limits_count_codepoints_without_combining_or_encoding() {
        // SDP-ID: supplementary, combining and lone-surrogate IDs are IR cases.
        for (limit, stream_mode) in [(64, true), (128, false)] {
            let ids = [
                PyString::from("🙂".repeat(limit).as_str()),
                PyString::from("e\u{0301}".repeat(limit / 2).as_str()),
                surrogate(),
                PyString::from(" "),
                PyString::from("\0"),
            ];
            for id in ids {
                if stream_mode {
                    let input = stream(id.clone(), protocol_rank(), None);
                    assert_eq!(
                        validate_stream_prefix(&input, CachePolicy::NoCache)
                            .unwrap()
                            .id(),
                        &id
                    );
                } else {
                    let input = embed(id.clone(), text("identity"), text(""));
                    match validate_embed_prefix(&input).unwrap() {
                        EmbedPrefix::NeedsInitializedIdentity(pending) => {
                            assert_eq!(pending.id(), &id)
                        }
                        other => panic!("wrong boundary: {other:?}"),
                    }
                }
            }
            for id in [
                PyString::from("🙂".repeat(limit + 1).as_str()),
                PyString::from(("e\u{0301}".repeat(limit / 2) + "x").as_str()),
                PyString::from(""),
            ] {
                if stream_mode {
                    let input = stream(id, protocol_rank(), None);
                    assert_refusal(
                        validate_stream_prefix(&input, CachePolicy::NoCache),
                        Site::StreamIdGuard,
                        FailureKind::ValueError,
                    );
                } else {
                    let input = embed(id, text("identity"), text(""));
                    assert_refusal(
                        validate_embed_prefix(&input),
                        Site::EmbedIdGuard,
                        FailureKind::ValueError,
                    );
                }
            }
        }
    }

    #[test]
    fn embed_common_guards_precede_operation_and_identity_requires_empty_text() {
        // R-2: whitespace is nonempty for identity, regardless of blankness.
        for body in [text(" "), text("\u{001c}"), Value::String(surrogate())] {
            let input = embed("id".into(), text("identity"), body);
            assert_refusal(
                validate_embed_prefix(&input),
                Site::EmbedIdentityText,
                FailureKind::ValueError,
            );
        }
        let identity = embed("id".into(), text("identity"), text(""));
        match validate_embed_prefix(&identity).unwrap() {
            EmbedPrefix::NeedsInitializedIdentity(pending) => {
                assert_eq!(pending.input(), &identity)
            }
            other => panic!("wrong boundary: {other:?}"),
        }
        let array = Value::Array(vec![object(vec![])]);
        assert_refusal(
            validate_embed_prefix(&array),
            Site::EmbedKeys,
            FailureKind::ValueError,
        );
        let missing = object(vec![("id", Value::Null), ("text", Value::Null)]);
        assert_refusal(
            validate_embed_prefix(&missing),
            Site::EmbedKeys,
            FailureKind::ValueError,
        );
        let extra = object(vec![
            ("id", Value::Null),
            ("operation", text("unknown")),
            ("text", Value::Null),
            ("extra", Value::Null),
        ]);
        assert_refusal(
            validate_embed_prefix(&extra),
            Site::EmbedKeys,
            FailureKind::ValueError,
        );
        let invalid_id = object(vec![
            ("id", Value::Bool(false)),
            ("operation", text("unknown")),
            ("text", Value::Null),
        ]);
        assert_refusal(
            validate_embed_prefix(&invalid_id),
            Site::EmbedIdGuard,
            FailureKind::ValueError,
        );
        let invalid_text = embed("id".into(), text("unknown"), Value::Null);
        assert_refusal(
            validate_embed_prefix(&invalid_text),
            Site::EmbedTextGuard,
            FailureKind::ValueError,
        );
        for operation in [text("unknown"), Value::Null, Value::Bool(false)] {
            let input = embed("id".into(), operation, text("body"));
            assert_refusal(
                validate_embed_prefix(&input),
                Site::EmbedOperation,
                FailureKind::ValueError,
            );
        }
    }

    #[test]
    fn embed_literal_byte_and_blank_cases_stop_before_tokenizer() {
        // Bound existing input IDs, with prefix outcomes only (never model acceptance).
        let positive = [
            ("query-ascii", "query", "database storage".to_owned()),
            ("query-4096-bytes", "query", "a ".repeat(2048)),
            (
                "query-token-empty",
                "query",
                "\u{200b}\u{200d}\u{feff}".to_owned(),
            ),
            (
                "document-ascii",
                "document",
                "Use the private directory.".to_owned(),
            ),
            ("document-maximum-cjk", "document", "漢".repeat(21845) + "!"),
            (
                "document-token-empty",
                "document",
                "\u{200b}\u{200d}\u{feff}".to_owned(),
            ),
        ];
        for (id, operation, body) in positive {
            let input = embed(id.into(), text(operation), text(&body));
            match validate_embed_prefix(&input).unwrap() {
                EmbedPrefix::NeedsTokenizer(pending) => {
                    assert_eq!(pending.utf8(), body.as_bytes());
                    assert_eq!(pending.input(), &input);
                    assert_eq!(
                        pending.operation(),
                        if operation == "query" {
                            TokenizerOperation::Query
                        } else {
                            TokenizerOperation::Document
                        }
                    );
                }
                other => panic!("wrong boundary for {id}: {other:?}"),
            }
        }
        let negative = [
            ("query-empty", "query", "".to_owned(), Site::EmbedQueryGuard),
            (
                "query-byte-overflow",
                "query",
                "a ".repeat(2048) + "a",
                Site::EmbedQueryGuard,
            ),
            (
                "document-empty",
                "document",
                "".to_owned(),
                Site::EmbedDocumentGuard,
            ),
            (
                "document-byte-overflow",
                "document",
                "!".repeat(65537),
                Site::EmbedDocumentGuard,
            ),
            (
                "blank-query-u001c",
                "query",
                "\u{001c}".to_owned(),
                Site::EmbedQueryGuard,
            ),
            (
                "blank-document-u0085",
                "document",
                "\u{0085}".to_owned(),
                Site::EmbedDocumentGuard,
            ),
            (
                "blank-query-u00a0",
                "query",
                "\u{00a0}".to_owned(),
                Site::EmbedQueryGuard,
            ),
            (
                "blank-document-u3000",
                "document",
                "\u{3000}".to_owned(),
                Site::EmbedDocumentGuard,
            ),
        ];
        for (id, operation, body, site) in negative {
            let input = embed(id.into(), text(operation), text(&body));
            assert_refusal(validate_embed_prefix(&input), site, FailureKind::ValueError);
        }
        for (operation, site) in [
            ("query", Site::EmbedQueryEncode),
            ("document", Site::EmbedDocumentEncode),
        ] {
            let input = embed(
                "SDP-SURROGATE".into(),
                text(operation),
                Value::String(surrogate()),
            );
            assert_refusal(
                validate_embed_prefix(&input),
                site,
                FailureKind::UnicodeEncodeError,
            );
        }
    }

    #[test]
    fn unqualified_echo_retains_versions_and_distinguishes_projection_failures() {
        // SDP-ECHO/R-3: nonfinite retention is NOT allow_nan=False reply acceptance.
        let versions = vec![
            Value::Null,
            Value::Bool(true),
            Value::String(surrogate()),
            Value::Array(vec![Value::Null, Value::Bool(false)]),
            object(vec![("nested", Value::FloatBits(0x7ff8_0000_0000_0042))]),
            Value::Integer(PyInteger::new(false, vec![u32::MAX; 256])),
            Value::Integer(PyInteger::new(true, vec![u32::MAX; 256])),
            Value::FloatBits(0x7ff8_0000_0000_0042),
            Value::FloatBits(0x7ff0_0000_0000_0000),
            Value::FloatBits(0xfff0_0000_0000_0000),
            Value::FloatBits(0x8000_0000_0000_0000),
        ];
        let notes: Vec<Value> = versions
            .iter()
            .map(|version| {
                object(vec![
                    ("record_id", Value::String(surrogate())),
                    ("version", version.clone()),
                    ("body_sha256", Value::Null),
                    ("extra", text("retained only in input")),
                ])
            })
            .collect();
        let echo = project_unqualified_rank_echo(&notes).unwrap();
        for (projected, expected) in echo.notes().iter().zip(&versions) {
            assert_eq!(projected.version(), expected);
            assert_eq!(projected.record_id(), &Value::String(surrogate()));
            assert_eq!(projected.body_sha256(), &Value::Null);
        }
        for (note, field) in [
            (object(vec![]), EchoField::RecordId),
            (object(vec![("record_id", Value::Null)]), EchoField::Version),
            (
                object(vec![("record_id", Value::Null), ("version", Value::Null)]),
                EchoField::BodySha256,
            ),
        ] {
            assert_eq!(
                project_unqualified_rank_echo(&[note]).unwrap_err(),
                ProjectionFailure::EchoLookup {
                    note_index: 0,
                    field,
                    kind: FailureKind::KeyError
                }
            );
        }
        let ordered = [notes[0].clone(), object(vec![]), Value::Null];
        assert_eq!(
            project_unqualified_rank_echo(&ordered).unwrap_err(),
            ProjectionFailure::EchoLookup {
                note_index: 1,
                field: EchoField::RecordId,
                kind: FailureKind::KeyError
            }
        );
        assert_eq!(
            project_unqualified_rank_echo(&[Value::Null]).unwrap_err(),
            ProjectionFailure::EchoLookup {
                note_index: 0,
                field: EchoField::RecordId,
                kind: FailureKind::TypeError
            }
        );
    }

    #[test]
    fn unqualified_nocache_projection_keeps_id_and_unchecked_logical_value() {
        let input = stream(surrogate(), protocol_rank(), None);
        let pending = validate_stream_prefix(&input, CachePolicy::NoCache).unwrap();
        let supplied = object(vec![("arbitrary", Value::FloatBits(0x7ff0_0000_0000_0000))]);
        let output = pending
            .unqualified_nocache_projection(UncheckedLogicalResult::new(&supplied))
            .unwrap();
        assert_eq!(output.id(), &surrogate());
        assert_eq!(output.result().value(), &supplied);
        assert_eq!(
            pending.rank().remaining_boundary(),
            RemainingRankBoundary::HashBodyThenLookupExpectedThenPythonInequality
        );
    }
}
