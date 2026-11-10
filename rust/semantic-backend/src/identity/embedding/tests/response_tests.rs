//! Independent orchestration fixtures. No native constructor, session, model,
//! getter, CPU observer or protected Python is invoked by these test bodies.
use crate::embedding_response::{self as response, *};
use cairn_semantic_protocol::{json, PyString, Value};
use cairn_semantic_rank::{backend::BackendError, IoStage, PendingProfile};
use cairn_semantic_tokenizer::{DocumentSpansError, PreparedInputs, PreparedTokenizer};
use std::{
    cell::RefCell,
    collections::VecDeque,
    io::{self, BufRead, Read, Write},
    rc::Rc,
};

type Trace = Rc<RefCell<Vec<String>>>;
const TOKENIZER: &str = r###"{
 "version":"1.0","truncation":null,"padding":null,"added_tokens":[],
 "normalizer":{"type":"BertNormalizer","clean_text":true,"handle_chinese_chars":true,"strip_accents":true,"lowercase":true},
 "pre_tokenizer":{"type":"BertPreTokenizer"},
 "post_processor":{"type":"BertProcessing","sep":["[SEP]",3],"cls":["[CLS]",2]},
 "decoder":{"type":"WordPiece","prefix":"##","cleanup":true},
 "model":{"type":"WordPiece","unk_token":"[UNK]","continuing_subword_prefix":"##","max_input_chars_per_word":100,
 "vocab":{"[PAD]":0,"[UNK]":1,"[CLS]":2,"[SEP]":3,"a":4,"abc":5,"e":6,"漢":7,"hello":8,"world":9,"##s":10,":":11}}
}"###;

fn prepared() -> PreparedTokenizer {
    PreparedTokenizer::from_bytes(PreparedInputs {
        config: br#"{"pad_token_id":0}"#,
        tokenizer_config: br#"{"model_max_length":6,"pad_token":"[PAD]"}"#,
        special_tokens_map:
            br#"{"pad_token":"[PAD]","unk_token":"[UNK]","cls_token":"[CLS]","sep_token":"[SEP]"}"#,
        tokenizer: TOKENIZER.as_bytes(),
    })
    .unwrap()
}

struct Session {
    report: super::ConstructionReport,
    tokenizer: PreparedTokenizer,
    batches: VecDeque<Result<Vec<FirstNormalizedRow>, BackendError>>,
    trace: Trace,
}
impl EmbeddingSession for Session {
    fn observations(&self) -> &super::ConstructionReport {
        self.trace.borrow_mut().push("observations".into());
        &self.report
    }
    fn tokenizer(&self) -> &PreparedTokenizer {
        self.trace.borrow_mut().push("tokenizer".into());
        &self.tokenizer
    }
    fn embed_first_normalized(
        &mut self,
        texts: &[String],
    ) -> Result<Vec<FirstNormalizedRow>, BackendError> {
        self.trace.borrow_mut().push(format!("embed:{texts:?}"));
        self.batches.pop_front().expect("unexpected embedding call")
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        self.trace.borrow_mut().push("drop-session".into());
    }
}
struct Factory {
    session: Session,
    fail: bool,
}
impl EmbeddingFactory for Factory {
    type Session = Session;
    fn construct(self) -> Result<Session, BackendError> {
        self.session.trace.borrow_mut().push("construct".into());
        if self.fail {
            Err(BackendError::Unavailable(
                "fixture construction failure".into(),
            ))
        } else {
            Ok(self.session)
        }
    }
}

struct Input {
    bytes: Vec<u8>,
    consumed: usize,
    chunk: usize,
    fail_at: Option<usize>,
    trace: Trace,
}
impl Read for Input {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        let available = self.fill_buf()?;
        let count = available.len().min(output.len());
        output[..count].copy_from_slice(&available[..count]);
        self.consume(count);
        Ok(count)
    }
}
impl BufRead for Input {
    fn fill_buf(&mut self) -> io::Result<&[u8]> {
        self.trace
            .borrow_mut()
            .push(format!("read:{}", self.consumed));
        if self.fail_at.is_some_and(|at| self.consumed >= at) {
            return Err(io::Error::other("fixture read"));
        }
        let stop = (self.consumed + self.chunk)
            .min(self.bytes.len())
            .min(self.fail_at.unwrap_or(usize::MAX));
        Ok(&self.bytes[self.consumed..stop])
    }
    fn consume(&mut self, count: usize) {
        self.consumed += count;
    }
}
struct Output {
    bytes: Vec<u8>,
    chunk: usize,
    fail_at: Option<usize>,
    zero: bool,
    fail_flush: bool,
    trace: Trace,
}
impl Write for Output {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.trace.borrow_mut().push("write".into());
        if self.zero {
            return Ok(0);
        }
        if self.fail_at.is_some_and(|at| self.bytes.len() >= at) {
            return Err(io::Error::other("fixture write"));
        }
        let count = bytes
            .len()
            .min(self.chunk)
            .min(self.fail_at.unwrap_or(usize::MAX) - self.bytes.len());
        self.bytes.extend_from_slice(&bytes[..count]);
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        self.trace.borrow_mut().push("flush".into());
        if self.fail_flush {
            Err(io::Error::other("fixture flush"))
        } else {
            Ok(())
        }
    }
}
fn setup(
    raw: Vec<u8>,
    batches: Vec<Result<Vec<FirstNormalizedRow>, BackendError>>,
) -> (Input, Output, Factory, Trace) {
    let trace = Rc::new(RefCell::new(vec![]));
    let input = Input {
        bytes: raw,
        consumed: 0,
        chunk: usize::MAX / 2,
        fail_at: None,
        trace: trace.clone(),
    };
    let output = Output {
        bytes: vec![],
        chunk: usize::MAX / 2,
        fail_at: None,
        zero: false,
        fail_flush: false,
        trace: trace.clone(),
    };
    let session = Session {
        report: super::fixture(),
        tokenizer: prepared(),
        batches: batches.into(),
        trace: trace.clone(),
    };
    (
        input,
        output,
        Factory {
            session,
            fail: false,
        },
        trace,
    )
}
fn row(first: f32) -> FirstNormalizedRow {
    let mut bits = vec![0.0f32.to_bits(); 384];
    bits[0] = first.to_bits();
    bits[1] = (-0.0f32).to_bits();
    FirstNormalizedRow {
        shape: vec![384],
        bits,
    }
}
fn request(operation: &str, text: &str) -> Vec<u8> {
    format!(
        "{{\"id\":\"r\",\"operation\":\"{operation}\",\"text\":{}}}\n",
        serde_json::to_string(text).unwrap()
    )
    .into_bytes()
}
fn decoded(bytes: &[u8]) -> Value {
    match json::decode_input(json::InputMode::EmbeddingLine, bytes).unwrap() {
        json::DecodeOutcome::Decoded(d) => d.into_value(),
        other => panic!("unexpected {other:?}"),
    }
}
fn field<'a>(value: &'a Value, key: &str) -> &'a Value {
    let Value::Object(object) = value else {
        panic!("expected object")
    };
    object.get(&PyString::from(key)).unwrap()
}
fn keys(value: &Value) -> Vec<String> {
    let Value::Object(object) = value else {
        panic!("expected object")
    };
    object
        .entries()
        .iter()
        .map(|(key, _)| String::from_utf8(key.strict_utf8().unwrap()).unwrap())
        .collect()
}
fn array(value: &Value) -> &[Value] {
    let Value::Array(rows) = value else {
        panic!("expected array")
    };
    rows
}
fn expected_identity() -> Value {
    decoded(format!("{{\"model_sha256\":\"{}\",\"algorithm\":\"bge-original-passages/1\",\"dimensions\":384}}\n",super::EMBED_DIGEST).as_bytes())
}
fn expected_vector(first: f64) -> Value {
    let mut values = vec![Value::FloatBits(0.0f64.to_bits()); 384];
    values[0] = Value::FloatBits(first.to_bits());
    values[1] = Value::FloatBits((-0.0f64).to_bits());
    Value::Array(values)
}
fn no_embedding(trace: &Trace) {
    assert!(!trace.borrow().iter().any(|x| x.starts_with("embed:")));
}

#[test]
fn embedding_response5_constructs_once_before_immediate_eof_and_drops() {
    let (mut i, mut o, f, t) = setup(vec![], vec![]);
    run_embedding(&mut i, &mut o, f).unwrap();
    assert_eq!(
        *t.borrow(),
        ["construct", "observations", "read:0", "drop-session"]
    );
    assert!(o.bytes.is_empty());
}
#[test]
fn embedding_response5_construction_failure_reads_and_writes_nothing() {
    let (mut i, mut o, mut f, t) = setup(request("identity", ""), vec![]);
    f.fail = true;
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(e.failure, Failure::Backend(_)));
    assert_eq!(*t.borrow(), ["construct", "drop-session"]);
    assert_eq!(i.consumed, 0);
    assert!(o.bytes.is_empty());
    assert!(e.input.is_empty());
}
#[test]
fn embedding_response5_null_observation_refuses_before_any_read_or_identity() {
    let (mut i, mut o, mut f, t) = setup(request("identity", ""), vec![]);
    f.session.report.inputs["session"]["cpu_arena"] = serde_json::Value::Null;
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    let Failure::Observations(gaps) = e.failure else {
        panic!("expected observations")
    };
    assert!(gaps.iter().any(|g| g.field == "session.cpu_arena"));
    assert_eq!(*t.borrow(), ["construct", "observations", "drop-session"]);
    assert_eq!(i.consumed, 0);
    assert!(o.bytes.is_empty());
}
#[test]
fn embedding_response5_identity_exact_bytes_after_initialization_without_model() {
    let (mut i, mut o, f, t) = setup(request("identity", ""), vec![]);
    run_embedding(&mut i, &mut o, f).unwrap();
    assert_eq!(o.bytes,format!("{{\"id\":\"r\",\"identity\":{{\"model_sha256\":\"{}\",\"algorithm\":\"bge-original-passages/1\",\"dimensions\":384}}}}\n",super::EMBED_DIGEST).as_bytes());
    assert_eq!(
        *t.borrow(),
        [
            "construct",
            "observations",
            "read:0",
            "write",
            "flush",
            &format!("read:{}", i.bytes.len()),
            "drop-session"
        ]
    );
    no_embedding(&t);
}
#[test]
fn embedding_response5_query_exact_landed_question_metadata_and_nonunit_signed_zero() {
    let (mut i, mut o, f, t) = setup(request("query", "abc"), vec![Ok(vec![row(2.0)])]);
    run_embedding(&mut i, &mut o, f).unwrap();
    let v = decoded(&o.bytes);
    assert_eq!(keys(&v), ["id", "identity", "query_projection", "vector"]);
    assert_eq!(field(&v, "identity"), &expected_identity());
    assert_eq!(field(&v, "vector"), &expected_vector(2.0));
    assert_eq!(field(&v,"query_projection"),&decoded(br#"{"method":"original-prefix/1","truncated":false,"original_tokens":11,"embedded_tokens":11,"prefix_bytes":3,"prefix_sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}
"#));
    assert_eq!(
        t.borrow()
            .iter()
            .filter(|x| x.starts_with("embed:"))
            .cloned()
            .collect::<Vec<_>>(),
        ["embed:[\"Represent this sentence for searching relevant passages: abc\"]"]
    );
}
#[test]
fn embedding_response5_document_all_original_passages_order_and_final_tail_spans() {
    let body = "a ".repeat(128);
    let (mut i, mut o, f, t) = setup(
        request("document", &body),
        vec![Ok(vec![row(2.0), row(3.0)])],
    );
    run_embedding(&mut i, &mut o, f).unwrap();
    let v = decoded(&o.bytes);
    assert_eq!(keys(&v), ["id", "identity", "passages"]);
    let p = array(field(&v, "passages"));
    assert_eq!(p.len(), 2);
    assert_eq!(
        field(&p[0], "span"),
        &decoded(b"{\"offset\":0,\"length\":255}\n")
    );
    assert_eq!(
        field(&p[1], "span"),
        &decoded(b"{\"offset\":192,\"length\":63}\n")
    );
    for (p, x) in p.iter().zip([2.0, 3.0]) {
        assert_eq!(keys(p), ["span", "vector"]);
        assert_eq!(field(p, "vector"), &expected_vector(x));
    }
    let expected = format!(
        "embed:{:?}",
        [
            format!("{}a", "a ".repeat(127)),
            format!("{}a", "a ".repeat(31))
        ]
    );
    assert_eq!(
        t.borrow()
            .iter()
            .filter(|x| x.starts_with("embed:"))
            .cloned()
            .collect::<Vec<_>>(),
        [expected]
    );
}
#[test]
fn embedding_response5_projection_error_precedes_any_embedding() {
    // Nonblank strict UTF-8 control input becomes empty under Bert clean_text.
    let (mut i, mut o, f, t) = setup(request("document", "\0"), vec![]);
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(
        e.failure,
        Failure::Document(DocumentSpansError::NoModelTokens)
    ));
    no_embedding(&t);
    assert!(o.bytes.is_empty());
    assert_eq!(t.borrow().last().unwrap(), "drop-session");
}
#[test]
fn embedding_response5_late_projection_failure_cannot_start_model_batch() {
    // Helper-only synthetic late failure: it proves response composition's
    // cutoff, not tokenizer offset correctness or actual native behavior.
    let (_, o, f, t) = setup(vec![], vec![]);
    let mut s = f.construct().unwrap();
    t.borrow_mut().push("projection:first-span".into());
    t.borrow_mut().push("projection:late-invalid-span".into());
    let e =
        response::document_vectors(&mut s, Err(DocumentSpansError::InvalidOffsets)).unwrap_err();
    assert!(matches!(
        e,
        Failure::Document(DocumentSpansError::InvalidOffsets)
    ));
    no_embedding(&t);
    assert!(o.bytes.is_empty());
    assert!(!t.borrow().iter().any(|x| x == "write" || x == "flush"));
    drop(s);
    assert_eq!(t.borrow().last().unwrap(), "drop-session");
}
#[test]
fn embedding_response5_full_batch_count_precedes_any_row_validation() {
    for rows in [vec![], vec![row(f32::NAN), row(1.0)]] {
        let actual = rows.len();
        let (mut i, mut o, f, _) = setup(request("query", "abc"), vec![Ok(rows)]);
        let e = run_embedding(&mut i, &mut o, f).unwrap_err();
        assert_eq!(
            e.failure.source_kind(),
            Some(cairn_semantic_protocol::FailureKind::ValueError)
        );
        assert!(matches!(e.failure,Failure::BatchCount {expected:1,actual:n} if n==actual));
        assert!(o.bytes.is_empty());
    }
}
#[test]
fn embedding_response5_wrong_declared_shape_and_storage_length_refuse() {
    let mut declared = row(1.0);
    declared.shape = vec![1, 384];
    let mut short = row(1.0);
    short.bits.pop();
    for bad in [declared, short] {
        let (mut i, mut o, f, _) = setup(request("query", "abc"), vec![Ok(vec![bad])]);
        let e = run_embedding(&mut i, &mut o, f).unwrap_err();
        assert!(matches!(e.failure, Failure::InvalidVector { row: 0 }));
        assert!(o.bytes.is_empty());
    }
}
#[test]
fn embedding_response5_nan_positive_and_negative_infinity_refuse() {
    for x in [f32::NAN, f32::INFINITY, f32::NEG_INFINITY] {
        let (mut i, mut o, f, _) = setup(request("query", "abc"), vec![Ok(vec![row(x)])]);
        let e = run_embedding(&mut i, &mut o, f).unwrap_err();
        assert!(matches!(e.failure, Failure::InvalidVector { row: 0 }));
        assert!(o.bytes.is_empty());
    }
}
#[test]
fn embedding_response5_all_positive_and_negative_zero_refuse() {
    for sign in [0.0f32, -0.0] {
        let bad = FirstNormalizedRow {
            shape: vec![384],
            bits: vec![sign.to_bits(); 384],
        };
        let (mut i, mut o, f, _) = setup(request("query", "abc"), vec![Ok(vec![bad])]);
        let e = run_embedding(&mut i, &mut o, f).unwrap_err();
        assert!(matches!(e.failure, Failure::InvalidVector { row: 0 }));
        assert!(o.bytes.is_empty());
    }
}
#[test]
fn embedding_response5_late_invalid_vector_leaves_no_partial_reply() {
    let (mut i, mut o, f, t) = setup(
        request("document", &"a ".repeat(128)),
        vec![Ok(vec![row(1.0), row(f32::NAN)])],
    );
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert_eq!(
        e.failure.source_kind(),
        Some(cairn_semantic_protocol::FailureKind::ValueError)
    );
    assert!(matches!(e.failure, Failure::InvalidVector { row: 1 }));
    assert_eq!(e.written, 0);
    assert!(o.bytes.is_empty());
    assert_eq!(t.borrow().last().unwrap(), "drop-session");
}
#[test]
fn embedding_response5_backend_failure_is_terminal_without_error_reply() {
    let first = request("query", "abc");
    let mut raw = first.clone();
    raw.extend(request("identity", ""));
    let (mut i, mut o, f, t) = setup(
        raw,
        vec![Err(BackendError::Operation {
            stage: "fixture embed",
            detail: "failure".into(),
        })],
    );
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(
        e.failure,
        Failure::Backend(BackendError::Operation {
            stage: "fixture embed",
            ..
        })
    ));
    assert_eq!(i.consumed, first.len());
    assert!(o.bytes.is_empty());
    assert_eq!(t.borrow().last().unwrap(), "drop-session");
}
#[test]
fn embedding_response5_exact_max_frame_including_newline_succeeds() {
    let mut raw = request("identity", "");
    raw.pop();
    raw.resize(json::EMBEDDING_INPUT_LIMIT - 1, b' ');
    raw.push(b'\n');
    let (mut i, mut o, f, _) = setup(raw, vec![]);
    run_embedding(&mut i, &mut o, f).unwrap();
    assert_eq!(i.consumed, json::EMBEDDING_INPUT_LIMIT);
    assert!(!o.bytes.is_empty());
}
#[test]
fn embedding_response5_overlong_frame_stops_at_cap_plus_one_before_json() {
    let raw = vec![b'x'; json::EMBEDDING_INPUT_LIMIT + 20];
    let (mut i, mut o, f, t) = setup(raw, vec![]);
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(
        e.failure,
        Failure::Codec(json::CodecError {
            site: json::CodecSite::InputSize,
            ..
        })
    ));
    assert_eq!(i.consumed, json::EMBEDDING_INPUT_LIMIT + 1);
    assert_eq!(e.input.len(), i.consumed);
    no_embedding(&t);
    assert!(o.bytes.is_empty());
}
#[test]
fn embedding_response5_missing_lf_precedes_unicode_and_semantic_validation() {
    let (mut i, mut o, f, t) = setup(vec![0xff], vec![]);
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(
        e.failure,
        Failure::Codec(json::CodecError {
            site: json::CodecSite::MissingLineFeed,
            ..
        })
    ));
    no_embedding(&t);
    assert!(o.bytes.is_empty());
}
#[test]
fn embedding_response5_whole_frame_unicode_precedes_json_syntax() {
    let (mut i, mut o, f, t) = setup(vec![b'!', 0xff, b'\n'], vec![]);
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(
        e.failure,
        Failure::Codec(json::CodecError {
            site: json::CodecSite::Unicode,
            ..
        })
    ));
    no_embedding(&t);
    assert!(o.bytes.is_empty());
}
#[test]
fn embedding_response5_malformed_and_truncated_json_stop_before_next_frame() {
    for first in [b"{\n".to_vec(), b"not-json\n".to_vec()] {
        let mut raw = first.clone();
        raw.extend(request("identity", ""));
        let (mut i, mut o, f, t) = setup(raw, vec![]);
        let e = run_embedding(&mut i, &mut o, f).unwrap_err();
        assert!(matches!(
            e.failure,
            Failure::Codec(json::CodecError {
                site: json::CodecSite::JsonSyntax,
                ..
            })
        ));
        assert_eq!(i.consumed, first.len());
        assert!(o.bytes.is_empty());
        no_embedding(&t);
    }
}
#[test]
fn embedding_response5_source_depth_is_typed_pending_before_unknown_key() {
    let raw = format!("{{\"unknown\":{}0{}}}\n", "[".repeat(65), "]".repeat(65)).into_bytes();
    let (mut i, mut o, f, t) = setup(raw, vec![]);
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(
        e.failure,
        Failure::PendingDepth(PendingProfile::Decode(_))
    ));
    no_embedding(&t);
    assert!(o.bytes.is_empty());
}
#[test]
fn embedding_response5_prefix_refusals_never_call_tokenizer_or_model() {
    for raw in [
        b"{\"id\":\"r\",\"operation\":\"identity\",\"text\":\"x\"}\n".to_vec(),
        request("unknown", "abc"),
        request("query", "\u{1c}"),
        request("query", &"a".repeat(4097)),
        request("document", &"a".repeat(65537)),
        b"{\"id\":\"r\",\"operation\":\"query\",\"text\":\"\\ud800\"}\n".to_vec(),
        b"{\"id\":true,\"operation\":\"query\",\"text\":\"abc\"}\n".to_vec(),
    ] {
        let (mut i, mut o, f, t) = setup(raw, vec![]);
        let e = run_embedding(&mut i, &mut o, f).unwrap_err();
        assert!(matches!(e.failure, Failure::Prefix(_)));
        no_embedding(&t);
        assert!(!t.borrow().iter().any(|x| x == "tokenizer"));
        assert!(o.bytes.is_empty());
    }
}
#[test]
fn embedding_response5_id_codepoints_and_surrogate_are_preserved() {
    let raw = b"{\"id\":\"\\ud800\",\"operation\":\"identity\",\"text\":\"\"}\n".to_vec();
    let (mut i, mut o, f, _) = setup(raw, vec![]);
    run_embedding(&mut i, &mut o, f).unwrap();
    assert_eq!(
        field(&decoded(&o.bytes), "id"),
        &Value::String(PyString::from_codepoints(vec![0xd800]).unwrap())
    );
}
#[test]
fn embedding_response5_each_reply_flushes_before_next_request_read() {
    let first = request("identity", "");
    let mut raw = first.clone();
    raw.extend(request("query", "abc"));
    raw.extend(request("document", "abc"));
    let (mut i, mut o, f, t) = setup(raw, vec![Ok(vec![row(2.0)]), Ok(vec![row(3.0)])]);
    run_embedding(&mut i, &mut o, f).unwrap();
    let lines = o
        .bytes
        .split_inclusive(|b| *b == b'\n')
        .map(decoded)
        .collect::<Vec<_>>();
    assert_eq!(lines.len(), 3);
    assert_eq!(field(&lines[0], "identity"), field(&lines[1], "identity"));
    assert_eq!(field(&lines[0], "identity"), field(&lines[2], "identity"));
    for line in &lines {
        assert_eq!(field(line, "id"), &Value::String(PyString::from("r")));
    }
    let trace = t.borrow();
    let flush = trace.iter().position(|x| x == "flush").unwrap();
    let next = trace
        .iter()
        .position(|x| x == &format!("read:{}", first.len()))
        .unwrap();
    assert!(flush < next);
    assert_eq!(trace.iter().filter(|x| *x == "construct").count(), 1);
    assert_eq!(trace.iter().filter(|x| *x == "flush").count(), 3);
    assert_eq!(trace.last().unwrap(), "drop-session");
}
#[test]
fn embedding_response5_chunked_reads_and_partial_successful_writes() {
    let (mut i, mut o, f, t) = setup(request("identity", ""), vec![]);
    i.chunk = 3;
    o.chunk = 7;
    run_embedding(&mut i, &mut o, f).unwrap();
    assert_eq!(field(&decoded(&o.bytes), "identity"), &expected_identity());
    assert!(t.borrow().iter().filter(|x| *x == "write").count() > 1);
    assert_eq!(t.borrow().iter().filter(|x| *x == "flush").count(), 1);
}
#[test]
fn embedding_response5_partial_read_failure_retains_frame_and_drops() {
    let (mut i, mut o, f, t) = setup(request("identity", ""), vec![]);
    i.fail_at = Some(5);
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(
        e.failure,
        Failure::Io {
            stage: IoStage::Read,
            ..
        }
    ));
    assert_eq!(e.input, &i.bytes[..5]);
    assert!(o.bytes.is_empty());
    assert_eq!(t.borrow().last().unwrap(), "drop-session");
}
#[test]
fn embedding_response5_write_zero_and_partial_failure_do_not_read_next_request() {
    for partial in [0, 7] {
        let first = request("identity", "");
        let mut raw = first.clone();
        raw.extend(request("identity", ""));
        let (mut i, mut o, f, t) = setup(raw, vec![]);
        if partial == 0 {
            o.zero = true;
        } else {
            o.fail_at = Some(partial);
        }
        let e = run_embedding(&mut i, &mut o, f).unwrap_err();
        assert!(matches!(
            e.failure,
            Failure::Io {
                stage: IoStage::Write,
                ..
            }
        ));
        assert_eq!(e.written, partial);
        assert_eq!(o.bytes.len(), partial);
        assert_eq!(i.consumed, first.len());
        assert!(!t.borrow().iter().any(|x| x == "flush"));
        assert_eq!(t.borrow().last().unwrap(), "drop-session");
    }
}
#[test]
fn embedding_response5_flush_failure_does_not_read_next_request() {
    let first = request("identity", "");
    let mut raw = first.clone();
    raw.extend(request("identity", ""));
    let (mut i, mut o, f, t) = setup(raw, vec![]);
    o.fail_flush = true;
    let e = run_embedding(&mut i, &mut o, f).unwrap_err();
    assert!(matches!(
        e.failure,
        Failure::Io {
            stage: IoStage::Flush,
            ..
        }
    ));
    assert_eq!(e.written, o.bytes.len());
    assert_eq!(i.consumed, first.len());
    assert_eq!(t.borrow().last().unwrap(), "drop-session");
}
#[test]
fn embedding_response5_nonfinite_serialization_fails_before_write() {
    let (_, mut o, _factory, t) = setup(vec![], vec![]);
    let (e, n) = response::write_reply(&mut o, &Value::FloatBits(f64::NAN.to_bits())).unwrap_err();
    assert!(matches!(
        e,
        Failure::Codec(json::CodecError {
            site: json::CodecSite::NonFinite,
            ..
        })
    ));
    assert_eq!(n, 0);
    assert!(o.bytes.is_empty());
    assert!(t.borrow().is_empty());
}
#[test]
fn embedding_response5_oversize_serialization_fails_before_write() {
    let (_, mut o, _factory, t) = setup(vec![], vec![]);
    let value = Value::String(PyString::from(
        "x".repeat(json::EMBEDDING_REPLY_LIMIT).as_str(),
    ));
    let (e, n) = response::write_reply(&mut o, &value).unwrap_err();
    assert!(matches!(
        e,
        Failure::Codec(json::CodecError {
            site: json::CodecSite::EmbeddingSize,
            ..
        })
    ));
    assert_eq!(n, 0);
    assert!(o.bytes.is_empty());
    assert!(t.borrow().is_empty());
}
#[test]
fn embedding_response5_render_depth_is_typed_pending_before_write() {
    let (_, mut o, _factory, t) = setup(vec![], vec![]);
    let mut value = Value::Null;
    for _ in 0..65 {
        value = Value::Array(vec![value]);
    }
    let (e, n) = response::write_reply(&mut o, &value).unwrap_err();
    assert!(matches!(
        e,
        Failure::PendingDepth(PendingProfile::Render(_))
    ));
    assert_eq!(n, 0);
    assert!(o.bytes.is_empty());
    assert!(t.borrow().is_empty());
}

#[test]
fn embedding_response5_float32_promotion_preserves_independent_exact_bits() {
    let mut r = row(1.0);
    r.bits[..4].copy_from_slice(&[0x3f800001, 0x00000001, 0x7f7fffff, 0x80000000]);
    let (mut i, mut o, f, _) = setup(request("query", "abc"), vec![Ok(vec![r])]);
    run_embedding(&mut i, &mut o, f).unwrap();
    let v = decoded(&o.bytes);
    assert_eq!(
        &array(field(&v, "vector"))[..4],
        &[
            Value::FloatBits(0x3ff0000020000000),
            Value::FloatBits(0x36a0000000000000),
            Value::FloatBits(0x47efffffe0000000),
            Value::FloatBits(0x8000000000000000),
        ]
    );
}

#[test]
fn embedding_response5_id_limit_counts_python_characters_instead_of_utf8_bytes() {
    for (count, accepted) in [(128, true), (129, false)] {
        let id = "🙂".repeat(count);
        let raw =
            format!("{{\"id\":{id:?},\"operation\":\"identity\",\"text\":\"\"}}\n").into_bytes();
        let (mut i, mut o, f, t) = setup(raw, vec![]);
        let result = run_embedding(&mut i, &mut o, f);
        if accepted {
            result.unwrap();
            assert_eq!(
                field(&decoded(&o.bytes), "id"),
                &Value::String(PyString::from(id.as_str()))
            );
        } else {
            assert!(matches!(result.unwrap_err().failure, Failure::Prefix(_)));
            assert!(o.bytes.is_empty());
        }
        no_embedding(&t);
    }
}
