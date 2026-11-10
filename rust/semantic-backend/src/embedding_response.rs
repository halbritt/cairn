//! Source embedding orchestration. Native construction/entry is separate.
use crate::identity::{ConstructionReport, MissingObservation};
use cairn_semantic_protocol::{
    json, EmbedPrefix, IrError, PyInteger, PyObject, PyString, Refusal, TokenizerOperation, Value,
};
use cairn_semantic_rank::{backend::BackendError, IoStage, PendingProfile};
use cairn_semantic_tokenizer::{DocumentSpansError, PreparedTokenizer, QueryPrefixError};
use std::io::{self, BufRead, Write};

/// One real owning, initialized collaborator is required in production. Trait
/// success and a digest alone are not worker/readiness qualification evidence.
pub trait EmbeddingFactory: Sized {
    type Session: EmbeddingSession;
    fn construct(self) -> Result<Self::Session, BackendError>;
}

pub trait EmbeddingSession {
    fn observations(&self) -> &ConstructionReport;
    fn tokenizer(&self) -> &PreparedTokenizer;
    /// Materialize the whole batch, in order, with batch-one FIRST-normalized
    /// float32 rows. No rank second normalization, scoring or cache is allowed.
    fn embed_first_normalized(
        &mut self,
        texts: &[String],
    ) -> Result<Vec<FirstNormalizedRow>, BackendError>;
}

#[derive(Debug)]
pub struct FirstNormalizedRow {
    pub shape: Vec<usize>,
    pub bits: Vec<u32>,
}

#[derive(Debug)]
pub enum Failure {
    Backend(BackendError),
    Observations(Vec<MissingObservation>),
    Prefix(Refusal),
    Query(QueryPrefixError),
    Document(DocumentSpansError),
    Codec(json::CodecError),
    PendingDepth(PendingProfile),
    BatchCount { expected: usize, actual: usize },
    InvalidVector { row: usize },
    Ir(IrError),
    Io { stage: IoStage, error: io::Error },
}

impl Failure {
    /// Preserve the Source semantic family where established. Codec JSON and
    /// Unicode decode families remain on CodecError; pending-depth, native,
    /// observation and I/O failures never acquire an invented Source family.
    pub fn source_kind(&self) -> Option<cairn_semantic_protocol::FailureKind> {
        use cairn_semantic_protocol::FailureKind;
        match self {
            Self::Backend(error) => error.source_kind(),
            Self::Prefix(error) => Some(error.kind),
            Self::Query(error) => error.source_kind(),
            Self::Document(error) => error.source_kind(),
            Self::BatchCount { .. } | Self::InvalidVector { .. } => Some(FailureKind::ValueError),
            Self::Codec(error) if error.family == json::ExceptionFamily::ValueError => {
                Some(FailureKind::ValueError)
            }
            _ => None,
        }
    }
}

#[derive(Debug)]
pub struct InvocationError {
    pub failure: Failure,
    pub input: Vec<u8>,
    pub written: usize,
}

fn object(entries: Vec<(&str, Value)>) -> Result<Value, Failure> {
    PyObject::new(
        entries
            .into_iter()
            .map(|(key, value)| (PyString::from(key), value))
            .collect(),
    )
    .map(Value::Object)
    .map_err(Failure::Ir)
}

fn integer(value: usize) -> Value {
    let limbs = value
        .to_le_bytes()
        .chunks(4)
        .map(|chunk| {
            let mut bytes = [0; 4];
            bytes[..chunk.len()].copy_from_slice(chunk);
            u32::from_le_bytes(bytes)
        })
        .collect();
    Value::Integer(PyInteger::new(false, limbs))
}

fn vectors(rows: Vec<FirstNormalizedRow>, expected: usize) -> Result<Vec<Value>, Failure> {
    if rows.len() != expected {
        return Err(Failure::BatchCount {
            expected,
            actual: rows.len(),
        });
    }
    // Match Source: materialize batch, count it, validate ALL rows, then convert.
    for (row, data) in rows.iter().enumerate() {
        if data.shape != [384]
            || data.bits.len() != 384
            || data.bits.iter().any(|b| !f32::from_bits(*b).is_finite())
            || !data.bits.iter().any(|b| f32::from_bits(*b) != 0.0)
        {
            return Err(Failure::InvalidVector { row });
        }
    }
    Ok(rows
        .into_iter()
        .map(|row| {
            Value::Array(
                row.bits
                    .into_iter()
                    .map(|b| Value::FloatBits((f32::from_bits(b) as f64).to_bits()))
                    .collect(),
            )
        })
        .collect())
}

fn read_line<R: BufRead>(input: &mut R, raw: &mut Vec<u8>) -> io::Result<()> {
    let cap = json::EMBEDDING_INPUT_LIMIT + 1;
    while raw.len() < cap {
        let available = input.fill_buf()?;
        if available.is_empty() {
            break;
        }
        let selected = &available[..available.len().min(cap - raw.len())];
        let count = selected
            .iter()
            .position(|b| *b == b'\n')
            .map_or(selected.len(), |i| i + 1);
        let ended = selected[count - 1] == b'\n';
        raw.extend_from_slice(&selected[..count]);
        input.consume(count);
        if ended {
            break;
        }
    }
    Ok(())
}

fn metadata(m: &cairn_semantic_tokenizer::QueryProjectionMetadata) -> Result<Value, Failure> {
    object(vec![
        ("method", Value::String(PyString::from(m.method))),
        ("truncated", Value::Bool(m.truncated)),
        ("original_tokens", integer(m.original_tokens)),
        ("embedded_tokens", integer(m.embedded_tokens)),
        ("prefix_bytes", integer(m.prefix_bytes)),
        (
            "prefix_sha256",
            Value::String(PyString::from(m.prefix_sha256.as_str())),
        ),
    ])
}

fn reply<S: EmbeddingSession>(
    session: &mut S,
    identity: &Value,
    request: &Value,
) -> Result<Value, Failure> {
    match cairn_semantic_protocol::validate_embed_prefix(request).map_err(Failure::Prefix)? {
        EmbedPrefix::NeedsInitializedIdentity(pending) => object(vec![
            ("id", Value::String(pending.id().clone())),
            ("identity", identity.clone()),
        ]),
        EmbedPrefix::NeedsTokenizer(pending) => {
            let mut fields = vec![
                ("id", Value::String(pending.id().clone())),
                ("identity", identity.clone()),
            ];
            match pending.operation() {
                TokenizerOperation::Query => {
                    let projection = session
                        .tokenizer()
                        .query_prefix(pending.text())
                        .map_err(Failure::Query)?;
                    let projection_metadata = metadata(&projection.metadata)?;
                    let rows = session
                        .embed_first_normalized(&[projection.question])
                        .map_err(Failure::Backend)?;
                    let mut result = vectors(rows, 1)?;
                    fields.push(("query_projection", projection_metadata));
                    fields.push(("vector", result.pop().expect("validated single-row batch")));
                }
                TokenizerOperation::Document => {
                    // Landed projection validates ALL spans and token limits
                    // before this first and only model-batch call.
                    let projection = session.tokenizer().document_passages(pending.text());
                    fields.push(("passages", document_vectors(session, projection)?));
                }
            }
            object(fields)
        }
    }
}

// Crate-private composition seam: production supplies ONLY the landed complete
// projection. Private tests can supply a late projection failure without
// reopening or duplicating the closed tokenizer's offset algorithm.
pub(crate) fn document_vectors<S: EmbeddingSession>(
    session: &mut S,
    projection: Result<Vec<cairn_semantic_tokenizer::DocumentPassage>, DocumentSpansError>,
) -> Result<Value, Failure> {
    let passages = projection.map_err(Failure::Document)?;
    let texts: Vec<_> = passages.iter().map(|p| p.text.clone()).collect();
    let rows = session
        .embed_first_normalized(&texts)
        .map_err(Failure::Backend)?;
    let values = vectors(rows, passages.len())?;
    let mut result = Vec::with_capacity(passages.len());
    for (passage, vector) in passages.into_iter().zip(values) {
        let span = object(vec![
            ("offset", integer(passage.span.offset)),
            ("length", integer(passage.span.length)),
        ])?;
        result.push(object(vec![("span", span), ("vector", vector)])?);
    }
    Ok(Value::Array(result))
}

pub(crate) fn write_reply<W: Write>(output: &mut W, reply: &Value) -> Result<(), (Failure, usize)> {
    // Complete rendering, including depth/conversion/size guards and final LF,
    // precedes the very first output write.
    let bytes = match json::encode_embedding_reply(reply).map_err(|e| (Failure::Codec(e), 0))? {
        json::EncodeOutcome::Complete(bytes) => bytes,
        json::EncodeOutcome::NeedsSourceDepth(marker) => {
            return Err((
                Failure::PendingDepth(PendingProfile::Render(marker.profile())),
                0,
            ))
        }
    };
    let mut written = 0;
    while written < bytes.len() {
        match output.write(&bytes[written..]) {
            Ok(0) => {
                return Err((
                    Failure::Io {
                        stage: IoStage::Write,
                        error: io::Error::new(
                            io::ErrorKind::WriteZero,
                            "reply write returned zero",
                        ),
                    },
                    written,
                ))
            }
            Ok(count) => written += count,
            Err(error) => {
                return Err((
                    Failure::Io {
                        stage: IoStage::Write,
                        error,
                    },
                    written,
                ))
            }
        }
    }
    output.flush().map_err(|error| {
        (
            Failure::Io {
                stage: IoStage::Flush,
                error,
            },
            written,
        )
    })
}

/// Owns one initialized collaborator for the invocation, before EVERY input
/// read, including immediate EOF. A production factory must establish actual
/// native readiness; this abstract source composition does not attest it.
/// Every failure is terminal. Request/reply locals drop before the next read,
/// and the session drops on normal EOF and every failure; no fallback/retry.
pub fn run_embedding<R: BufRead, W: Write, F: EmbeddingFactory>(
    input: &mut R,
    output: &mut W,
    factory: F,
) -> Result<(), InvocationError> {
    let initial = |failure| InvocationError {
        failure,
        input: vec![],
        written: 0,
    };
    let mut session = factory
        .construct()
        .map_err(|e| initial(Failure::Backend(e)))?;
    let digest = session
        .observations()
        .embedding_identity()
        .map_err(|e| initial(Failure::Observations(e)))?;
    let identity = object(vec![
        ("model_sha256", Value::String(digest)),
        (
            "algorithm",
            Value::String(PyString::from("bge-original-passages/1")),
        ),
        ("dimensions", integer(384)),
    ])
    .map_err(initial)?;
    loop {
        let mut raw = Vec::new();
        if let Err(error) = read_line(input, &mut raw) {
            return Err(InvocationError {
                failure: Failure::Io {
                    stage: IoStage::Read,
                    error,
                },
                input: raw,
                written: 0,
            });
        }
        if raw.is_empty() {
            return Ok(());
        }
        let result = (|| {
            let request = match json::decode_input(json::InputMode::EmbeddingLine, &raw)
                .map_err(Failure::Codec)?
            {
                json::DecodeOutcome::Decoded(decoded) => decoded.into_value(),
                json::DecodeOutcome::EndOfInput => return Ok(None),
                json::DecodeOutcome::NeedsSourceDepth(marker) => {
                    return Err(Failure::PendingDepth(PendingProfile::Decode(
                        marker.profile(),
                    )))
                }
            };
            reply(&mut session, &identity, &request).map(Some)
        })();
        let response = match result {
            Ok(Some(response)) => response,
            Ok(None) => return Ok(()),
            Err(failure) => {
                return Err(InvocationError {
                    failure,
                    input: raw,
                    written: 0,
                })
            }
        };
        if let Err((failure, written)) = write_reply(output, &response) {
            return Err(InvocationError {
                failure,
                input: raw,
                written,
            });
        }
    }
}
