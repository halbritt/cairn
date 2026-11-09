//! Scripted source component checks, not native tokenizer/model/numeric evidence.
use super::backend::*;
use super::*;
use crate::rank::{string, Engine};
use cairn_semantic_protocol::hash;
use cairn_semantic_protocol::json::{self, CodecSite, EncodeOutcome, InputMode};
use cairn_semantic_protocol::{CachePolicy, EchoField, PyInteger, PyObject, PyString, Site, Value};
use std::cell::RefCell;
use std::collections::VecDeque;
use std::io::{self, BufRead, Cursor, Read, Write};
use std::rc::Rc;

const BODY_HASH: &str = "230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5";
const A_HASH: &str = "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb";
const QUESTION: &str = "Represent this sentence for searching relevant passages: q";
const RESULT: &str = concat!("{\"model_sha256\": \"model\", \"algorithm\": \"bge-max-chunk/1\", \"scores\": [{\"record_id\": \"r\", \"version\": 1, \"body_sha256\": \"230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5\", \"score\": 250000}]}");

fn object(entries: Vec<(&str, Value)>) -> Value {
    Value::Object(
        PyObject::new(
            entries
                .into_iter()
                .map(|(key, value)| (string(key), value))
                .collect(),
        )
        .unwrap(),
    )
}
fn text(value: &str) -> Value {
    Value::String(string(value))
}
fn integer(value: u32) -> Value {
    Value::Integer(PyInteger::new(false, vec![value]))
}
fn note() -> Value {
    object(vec![
        ("body", text("body")),
        ("body_sha256", text(BODY_HASH)),
        ("record_id", text("r")),
        ("version", integer(1)),
    ])
}
fn request(notes: Vec<Value>) -> Value {
    object(vec![("notes", Value::Array(notes)), ("query", text("q"))])
}
fn envelope(request: Value, cache: Option<Value>) -> Value {
    let mut entries = vec![("id", text("x")), ("request", request)];
    if let Some(cache) = cache {
        entries.push(("cache", cache));
    }
    object(entries)
}
fn change(value: &Value, key: &str, replacement: Option<Value>) -> Value {
    let Value::Object(value) = value else {
        panic!("fixture object");
    };
    let mut entries = value.entries().to_vec();
    entries.retain(|(name, _)| name != &string(key));
    if let Some(replacement) = replacement {
        entries.push((string(key), replacement));
    }
    Value::Object(PyObject::new(entries).unwrap())
}
fn wire(value: &Value) -> Vec<u8> {
    match json::encode_rank_once(value).unwrap() {
        EncodeOutcome::Complete(bytes) => bytes,
        _ => panic!("fixture must be shallow"),
    }
}
fn raw(row_bits: &[u32]) -> RawMatrix {
    RawMatrix {
        rows: row_bits.len(),
        columns: 384,
        bits: row_bits.iter().flat_map(|bits| vec![*bits; 384]).collect(),
    }
}
#[derive(Debug)]
enum TokenStep {
    Encode(PyString, SpecialTokens, Vec<u32>),
    Decode(Vec<u32>, PyString),
}
#[derive(Default)]
struct Script {
    steps: VecDeque<TokenStep>,
    embeddings: VecDeque<RawMatrix>,
    similarities: VecDeque<Vec<f32>>,
    zero: bool,
    fail: Option<&'static str>,
    events: Vec<&'static str>,
    model_inputs: Vec<Vec<PyString>>,
    matrices: Vec<RawMatrix>,
}
impl Script {
    fn query(&mut self, count: usize) {
        self.encode(QUESTION, SpecialTokens::Default, vec![99; count]);
    }
    fn encode(&mut self, text: &str, specials: SpecialTokens, tokens: Vec<u32>) {
        self.steps
            .push_back(TokenStep::Encode(string(text), specials, tokens));
    }
    fn decode(&mut self, tokens: Vec<u32>, text: &str) {
        self.steps
            .push_back(TokenStep::Decode(tokens, string(text)));
    }
    fn single(&mut self, chunk: &str) {
        self.query(1);
        self.encode("body", SpecialTokens::Disabled, vec![7]);
        self.decode(vec![7], chunk);
        self.encode(chunk, SpecialTokens::Default, vec![8]);
    }
    fn infer(&mut self, rows: &[u32], similarities: &[f32]) {
        self.embeddings.push_back(raw(rows));
        self.similarities.push_back(similarities.to_vec());
    }
    fn enter(&mut self, stage: &'static str) -> Result<(), BackendError> {
        self.events.push(stage);
        if self.fail == Some(stage) {
            Err(BackendError::Operation {
                stage,
                detail: "selected test failure".into(),
            })
        } else {
            Ok(())
        }
    }
}
#[derive(Clone)]
struct Factory(Rc<RefCell<Script>>);
struct Session(Rc<RefCell<Script>>, PyString);
impl BackendFactory for Factory {
    type Session = Session;
    fn construct(&mut self) -> Result<Session, BackendError> {
        self.0.borrow_mut().enter("construct")?;
        Ok(Session(self.0.clone(), string("model")))
    }
}
impl BackendSession for Session {
    type Norms = ();
    type Normalized = RawMatrix;
    fn identity(&self) -> &PyString {
        self.0.borrow_mut().events.push("identity");
        &self.1
    }
    fn encode(&mut self, text: &PyString, policy: SpecialTokens) -> Result<Vec<u32>, BackendError> {
        let mut script = self.0.borrow_mut();
        script.enter("encode")?;
        match script.steps.pop_front().expect("unexpected encode") {
            TokenStep::Encode(expected, specials, tokens) => {
                assert_eq!(text, &expected);
                assert_eq!(policy, specials);
                Ok(tokens)
            }
            other => panic!("encode before {other:?}"),
        }
    }
    fn decode(&mut self, tokens: &[u32], policy: DecodeTokens) -> Result<PyString, BackendError> {
        assert_eq!(policy, DecodeTokens::KeepSpecialTokens);
        let mut script = self.0.borrow_mut();
        script.enter("decode")?;
        match script.steps.pop_front().expect("unexpected decode") {
            TokenStep::Decode(expected, text) => {
                assert_eq!(tokens, expected);
                Ok(text)
            }
            other => panic!("decode before {other:?}"),
        }
    }
    fn embed_batch_one(&mut self, texts: &[PyString]) -> Result<RawMatrix, BackendError> {
        let mut script = self.0.borrow_mut();
        script.enter("embed_batch_one")?;
        script.model_inputs.push(texts.to_vec());
        Ok(script.embeddings.pop_front().expect("unexpected inference"))
    }
    fn norms_keepdims(&mut self, matrix: &RawMatrix) -> Result<(), BackendError> {
        let mut script = self.0.borrow_mut();
        script.enter("norms_keepdims")?;
        script.matrices.push(matrix.clone());
        Ok(())
    }
    fn any_zero_norm(&mut self, _: &()) -> Result<bool, BackendError> {
        let mut script = self.0.borrow_mut();
        script.enter("any_zero_norm")?;
        Ok(script.zero)
    }
    fn divide_in_place(&mut self, matrix: RawMatrix, _: ()) -> Result<RawMatrix, BackendError> {
        let mut script = self.0.borrow_mut();
        script.enter("divide_in_place")?;
        assert_eq!(script.matrices.last(), Some(&matrix));
        Ok(matrix)
    }
    fn matrix_vector(&mut self, _: RawMatrix) -> Result<Vec<f32>, BackendError> {
        let mut script = self.0.borrow_mut();
        script.enter("matrix_vector")?;
        Ok(script.similarities.pop_front().expect("unexpected matvec"))
    }
}
fn new_engine(script: Script) -> (Engine<Factory>, Rc<RefCell<Script>>) {
    let state = Rc::new(RefCell::new(script));
    (Engine::new(Factory(state.clone())), state)
}
fn score(engine: &mut Engine<Factory>, request: &Value) -> Result<Value, StageError> {
    let admitted = hash::validate_rank_hashes(request).map_err(Failure::Prefix)?;
    engine.construct()?;
    engine.score(&admitted)
}
fn source_at(error: StageError, site: SourceSite) {
    assert!(
        matches!(error,StageError::Failure(Failure::Source{site:actual,kind:FailureKind::ValueError}) if actual==site)
    );
}
fn success_script() -> Script {
    let mut script = Script::default();
    script.single("a");
    script.infer(&[0x3f80_0000, 0x4000_0000], &[0.25]);
    script
}

/// R1/R2: literal bytes cross the entire decoder/admission/scoring/encoder seam.
#[test]
fn rank_once_literal_output_and_operation_order() {
    let state = Rc::new(RefCell::new(success_script()));
    let mut output = Vec::new();
    run_rank_once(
        &mut Cursor::new(wire(&request(vec![note()]))),
        &mut output,
        Factory(state.clone()),
    )
    .unwrap();
    assert_eq!(output, format!("{RESULT}\n").as_bytes());
    let script = state.borrow();
    assert_eq!(
        script.events,
        [
            "construct",
            "identity",
            "encode",
            "encode",
            "decode",
            "encode",
            "embed_batch_one",
            "norms_keepdims",
            "any_zero_norm",
            "divide_in_place",
            "matrix_vector"
        ]
    );
    assert_eq!(script.model_inputs, [vec![string(QUESTION), string("a")]]);
    assert_eq!(script.matrices, [raw(&[0x3f80_0000, 0x4000_0000])]);
    assert!(script.steps.is_empty());
}

/// R3: bad bytes/prefix/hash cannot invoke even the factory; no partial output.
#[test]
fn admission_failures_precede_constructor() {
    let bad_hash = change(&note(), "body_sha256", Some(text("bad")));
    for bytes in [
        b"{".to_vec(),
        wire(&object(vec![])),
        wire(&request(vec![bad_hash])),
    ] {
        let state = Rc::new(RefCell::new(Script::default()));
        let mut output = Vec::new();
        let error = run_rank_once(
            &mut Cursor::new(bytes.clone()),
            &mut output,
            Factory(state.clone()),
        )
        .unwrap_err();
        assert_eq!(error.input, bytes);
        assert_eq!(error.written, 0);
        assert!(output.is_empty());
        assert!(state.borrow().events.is_empty());
    }
}

/// D1/D2: query512 accepted,513 refuses before any body encode/inference.
#[test]
fn query_default_token_512_513_boundary() {
    for count in [512, 513] {
        let mut script = Script::default();
        script.query(count);
        if count == 512 {
            script.encode("body", SpecialTokens::Disabled, vec![7]);
            script.decode(vec![7], "a");
            script.encode("a", SpecialTokens::Default, vec![8]);
            script.infer(&[0x3f80_0000, 0x4000_0000], &[0.25]);
        }
        let (mut engine, state) = new_engine(script);
        let result = score(&mut engine, &request(vec![note()]));
        if count == 512 {
            assert!(result.is_ok());
        } else {
            source_at(result.unwrap_err(), SourceSite::QueryModelTokens);
            assert_eq!(state.borrow().events, ["construct", "identity", "encode"]);
        }
    }
}

/// D2: no model tokens is the Source ValueError and performs zero model calls.
#[test]
fn empty_body_model_tokens_refuse_without_model() {
    let mut script = Script::default();
    script.query(1);
    script.encode("body", SpecialTokens::Disabled, vec![]);
    let (mut engine, state) = new_engine(script);
    source_at(
        score(&mut engine, &request(vec![note()])).unwrap_err(),
        SourceSite::BodyModelTokens(0),
    );
    assert_eq!(
        state.borrow().events,
        ["construct", "identity", "encode", "encode"]
    );
}

/// D1/D2: literal slice boundaries catch stride/end-break generator regressions.
#[test]
fn chunk_windows_384_385_704_705_are_1_2_2_3() {
    let cases: &[(usize, &[(usize, usize)])] = &[
        (384, &[(0, 384)]),
        (385, &[(0, 384), (320, 385)]),
        (704, &[(0, 384), (320, 704)]),
        (705, &[(0, 384), (320, 704), (640, 705)]),
    ];
    for &(length, slices) in cases {
        let mut script = Script::default();
        script.query(1);
        script.encode(
            "body",
            SpecialTokens::Disabled,
            (0..length as u32).collect(),
        );
        for &(start, end) in slices {
            script.decode((start as u32..end as u32).collect(), "a");
            script.encode("a", SpecialTokens::Default, vec![8]);
        }
        script.infer(&[0x3f80_0000, 0x4000_0000], &vec![0.25; slices.len()]);
        let (mut engine, state) = new_engine(script);
        score(&mut engine, &request(vec![note()])).unwrap();
        assert!(state.borrow().steps.is_empty());
        assert_eq!(state.borrow().matrices[0].rows, slices.len() + 1);
        assert_eq!(
            state.borrow().model_inputs[0],
            [string(QUESTION), string("a")]
        );
    }
}

/// D1/D2: rejected reencode stops before the next decode AND next body encode.
#[test]
fn chunk_default_token_512_513_boundary_is_lazy() {
    for count in [512, 513] {
        let mut script = Script::default();
        script.query(1);
        script.encode("body", SpecialTokens::Disabled, (0..385).collect());
        script.decode((0..384).collect(), "a");
        script.encode("a", SpecialTokens::Default, vec![8; count]);
        if count == 512 {
            script.decode((320..385).collect(), "b");
            script.encode("b", SpecialTokens::Default, vec![9]);
            script.encode("body", SpecialTokens::Disabled, vec![7]);
            script.decode(vec![7], "a");
            script.encode("a", SpecialTokens::Default, vec![8]);
            script.infer(&[0x3f80_0000, 0x4000_0000, 0x4040_0000], &[0.25, 0.5, 0.25]);
        }
        let (mut engine, state) = new_engine(script);
        let result = score(&mut engine, &request(vec![note(), note()]));
        if count == 512 {
            assert!(result.is_ok());
            assert!(state.borrow().steps.is_empty());
        } else {
            source_at(result.unwrap_err(), SourceSite::PassageModelTokens);
            assert_eq!(
                state.borrow().events,
                [
                    "construct",
                    "identity",
                    "encode",
                    "encode",
                    "decode",
                    "encode"
                ]
            );
        }
    }
}

/// R4/R5: unique misses first order, ALL duplicate occurrence rows reconstructed.
#[test]
fn unique_miss_order_and_duplicate_raw_reconstruction() {
    let mut script = Script::default();
    script.query(1);
    script.encode("body", SpecialTokens::Disabled, (0..705).collect());
    for (start, end, text) in [(0, 384, "b"), (320, 704, "a"), (640, 705, "b")] {
        script.decode((start..end).collect(), text);
        script.encode(text, SpecialTokens::Default, vec![8]);
    }
    script.infer(&[0x3f80_0000, 0x4000_0000, 0x4040_0000], &[0.25, 0.5, 0.75]);
    let (mut engine, state) = new_engine(script);
    let result = score(&mut engine, &request(vec![note()])).unwrap();
    assert_eq!(
        state.borrow().model_inputs,
        [vec![string(QUESTION), string("b"), string("a")]]
    );
    assert_eq!(
        state.borrow().matrices,
        [raw(&[0x3f80_0000, 0x4000_0000, 0x4040_0000, 0x4000_0000])]
    );
    assert_eq!(engine.cached().len(), 2);
    assert_eq!(engine.cached()[1].0, string(A_HASH));
    assert_eq!(engine.cached()[0].1, vec![0x4000_0000; 384]);
    let Value::Object(result) = result else {
        panic!()
    };
    let Some(Value::Array(scores)) = result.get(&string("scores")) else {
        panic!()
    };
    let Value::Object(first) = &scores[0] else {
        panic!()
    };
    assert_eq!(first.get(&string("score")), Some(&integer(750_000)));
}

/// R4/R5: warm cache still invokes model once for query and repeats raw hits.
#[test]
fn warm_cache_uses_query_only_model_and_raw_hit_rows() {
    let mut script = success_script();
    script.single("a");
    script.infer(&[0x4040_0000], &[0.5]);
    let (mut engine, state) = new_engine(script);
    let request = request(vec![note()]);
    score(&mut engine, &request).unwrap();
    score(&mut engine, &request).unwrap();
    assert_eq!(
        state.borrow().model_inputs,
        [vec![string(QUESTION), string("a")], vec![string(QUESTION)]]
    );
    assert_eq!(state.borrow().matrices[1], raw(&[0x4040_0000, 0x4000_0000]));
    assert_eq!(engine.cached(), &[(string(A_HASH), vec![0x4000_0000; 384])]);
}

/// D2: duplicates/cache hits count;129th decoded BEFORE refusing, not encoded.
#[test]
fn occurrence_129_counts_duplicate_hits_after_decode() {
    for (length, accepted) in [(41024, true), (41025, false)] {
        let mut script = success_script();
        script.query(1);
        // Literal end-break edges:128th ends41024;129th starts40960.
        script.encode("body", SpecialTokens::Disabled, (0..length).collect());
        for index in 0..128u32 {
            let start = index * 320;
            script.decode((start..start + 384).collect(), "a");
            script.encode("a", SpecialTokens::Default, vec![8]);
        }
        if accepted {
            script.infer(&[0x4040_0000], &vec![0.25; 128]);
        } else {
            script.decode((40960..41025).collect(), "a");
        }
        let (mut engine, state) = new_engine(script);
        score(&mut engine, &request(vec![note()])).unwrap();
        let result = score(&mut engine, &request(vec![note()]));
        if accepted {
            assert!(result.is_ok());
        } else {
            source_at(result.unwrap_err(), SourceSite::OccurrenceBudget);
            assert_eq!(state.borrow().events.last(), Some(&"decode"));
        }
        let script = state.borrow();
        assert!(script.steps.is_empty());
        assert_eq!(script.model_inputs.len(), if accepted { 2 } else { 1 });
        assert_eq!(
            script
                .events
                .iter()
                .filter(|stage| **stage == "decode")
                .count(),
            if accepted { 129 } else { 130 }
        );
        assert_eq!(engine.cached(), &[(string(A_HASH), vec![0x4000_0000; 384])]);
    }
}

/// R5: model shape/finite checks precede norms, including wrong row/col/length.
#[test]
fn raw_model_shape_and_nonfinite_refuse_before_norms() {
    let mut cases = vec![
        raw(&[0x3f80_0000]),
        raw(&[0x3f80_0000, 0x7f80_0000]),
        raw(&[0x3f80_0000, 0x7fc0_0001]),
    ];
    let mut columns = raw(&[0x3f80_0000, 0x4000_0000]);
    columns.columns = 383;
    cases.push(columns);
    let mut length = raw(&[0x3f80_0000, 0x4000_0000]);
    length.bits.pop();
    cases.push(length);
    for matrix in cases {
        let mut script = success_script();
        script.embeddings = VecDeque::from([matrix]);
        let (mut engine, state) = new_engine(script);
        source_at(
            score(&mut engine, &request(vec![note()])).unwrap_err(),
            SourceSite::RawModelShapeOrFinite,
        );
        assert_eq!(state.borrow().events.last(), Some(&"embed_batch_one"));
        assert!(engine.cached().is_empty());
    }
}

/// R5: opaque backend norms then zero check; divide/matvec never run on zero.
#[test]
fn zero_norm_stops_before_division() {
    let mut script = success_script();
    script.zero = true;
    let (mut engine, state) = new_engine(script);
    source_at(
        score(&mut engine, &request(vec![note()])).unwrap_err(),
        SourceSite::ZeroNorm,
    );
    assert_eq!(
        &state.borrow().events[7..],
        ["norms_keepdims", "any_zero_norm"]
    );
    assert!(engine.cached().is_empty());
}

/// R5/R6: no new post-arithmetic guard; late Source reduction handles NaN/Inf.
#[test]
fn supplied_nonfinite_similarities_use_published_reduction() {
    for (similarity, expected) in [
        (f32::NAN, -1_000_000_i32),
        (f32::INFINITY, 1_000_000),
        (f32::NEG_INFINITY, -1_000_000),
    ] {
        let mut script = success_script();
        script.similarities = VecDeque::from([vec![similarity]]);
        let (mut engine, _) = new_engine(script);
        let result = score(&mut engine, &request(vec![note()])).unwrap();
        let Value::Object(result) = result else {
            panic!()
        };
        let Some(Value::Array(scores)) = result.get(&string("scores")) else {
            panic!()
        };
        let Value::Object(first) = &scores[0] else {
            panic!()
        };
        assert_eq!(
            first.get(&string("score")),
            Some(&Value::Integer(PyInteger::new(
                expected < 0,
                vec![expected.unsigned_abs()]
            )))
        );
    }
}

/// R6/D1: metadata is looked up after matvec; missing fields remain KeyError.
#[test]
fn late_metadata_keyerror_preserves_old_cache() {
    for (key, field) in [
        ("record_id", EchoField::RecordId),
        ("version", EchoField::Version),
    ] {
        let mut script = success_script();
        script.single("b");
        script.infer(&[0x4040_0000, 0x4080_0000], &[0.75]);
        let (mut engine, state) = new_engine(script);
        score(&mut engine, &request(vec![note()])).unwrap();
        let failure = score(&mut engine, &request(vec![change(&note(), key, None)])).unwrap_err();
        assert!(
            matches!(failure,StageError::Failure(Failure::Projection(ProjectionFailure::EchoLookup{note_index:0,field:actual,kind:FailureKind::KeyError})) if actual==field)
        );
        assert_eq!(state.borrow().events.last(), Some(&"matrix_vector"));
        assert_eq!(engine.cached(), &[(string(A_HASH), vec![0x4000_0000; 384])]);
    }
}

/// R6: successful replacement drops old keys and retains independent raw copies.
#[test]
fn successful_cache_replacement_keeps_only_current_passages() {
    let mut script = success_script();
    script.single("b");
    script.infer(&[0x4040_0000, 0x4080_0000], &[0.75]);
    let (mut engine, _) = new_engine(script);
    score(&mut engine, &request(vec![note()])).unwrap();
    let snapshot = engine.snapshot().unwrap();
    score(&mut engine, &request(vec![note()])).unwrap();
    assert_eq!(engine.cached().len(), 1);
    assert_ne!(engine.cached()[0].0, string(A_HASH));
    assert_eq!(engine.cached()[0].1, vec![0x4080_0000; 384]);
    let Value::Object(snapshot) = snapshot else {
        panic!()
    };
    let Some(Value::Object(vectors)) = snapshot.get(&string("vectors")) else {
        panic!()
    };
    assert!(vectors.get(&string(A_HASH)).is_some());
}

/// R3/R6: construct/identity BEFORE restore, then warm hit plus query-only model.
#[test]
fn first_restore_uses_constructed_session_identity() {
    let (mut seed, _) = new_engine(success_script());
    score(&mut seed, &request(vec![note()])).unwrap();
    let snapshot = seed.snapshot().unwrap();
    let mut script = Script::default();
    script.single("a");
    script.infer(&[0x4040_0000], &[0.5]);
    let (mut engine, state) = new_engine(script);
    crate::stream::stream_reply(
        &mut engine,
        &wire(&envelope(request(vec![note()]), Some(snapshot))),
        CachePolicy::AdvertisedCache,
    )
    .unwrap();
    assert_eq!(&state.borrow().events[..2], ["construct", "identity"]);
    assert_eq!(state.borrow().model_inputs, [vec![string(QUESTION)]]);
    assert_eq!(state.borrow().matrices, [raw(&[0x4040_0000, 0x4000_0000])]);
    let (mut engine, state) = new_engine(Script::default());
    let error = crate::stream::stream_reply(
        &mut engine,
        &wire(&envelope(request(vec![note()]), Some(integer(1)))),
        CachePolicy::AdvertisedCache,
    )
    .unwrap_err();
    assert!(matches!(error, StageError::Failure(Failure::Cache(_))));
    assert_eq!(state.borrow().events, ["construct", "identity"]);
}

/// R3: other-model restoration ignored, active presentNull refuses AFTER hashes.
#[test]
fn other_identity_and_active_present_null_follow_policy() {
    let ignored = object(vec![
        ("format", text("cairn.passage-vectors/1")),
        ("model_sha256", text("other")),
        ("vectors", Value::Null),
    ]);
    let (mut engine, state) = new_engine(success_script());
    crate::stream::stream_reply(
        &mut engine,
        &wire(&envelope(request(vec![note()]), Some(ignored))),
        CachePolicy::AdvertisedCache,
    )
    .unwrap();
    let before = state.borrow().events.len();
    let bad = change(&note(), "body_sha256", Some(text("bad")));
    let error = crate::stream::stream_reply(
        &mut engine,
        &wire(&envelope(request(vec![bad]), Some(Value::Null))),
        CachePolicy::AdvertisedCache,
    )
    .unwrap_err();
    assert!(matches!(
        error,
        StageError::Failure(Failure::Prefix(cairn_semantic_protocol::Refusal {
            site: Site::RankBodyHashMismatch(0),
            ..
        }))
    ));
    let error = crate::stream::stream_reply(
        &mut engine,
        &wire(&envelope(request(vec![note()]), Some(Value::Null))),
        CachePolicy::AdvertisedCache,
    )
    .unwrap_err();
    assert!(matches!(
        error,
        StageError::Failure(Failure::Prefix(cairn_semantic_protocol::Refusal {
            site: Site::StreamLateCache,
            ..
        }))
    ));
    assert_eq!(state.borrow().events.len(), before);
}

/// R6: ordinary65536-byte guard must run BEFORE snapshot serialization.
#[test]
fn ordinary_reply_size_guard_precedes_snapshot() {
    let note = change(&note(), "record_id", Some(text(&"r".repeat(65536))));
    let (mut engine, state) = new_engine(success_script());
    let error = crate::stream::stream_reply(
        &mut engine,
        &wire(&envelope(request(vec![note]), None)),
        CachePolicy::AdvertisedCache,
    )
    .unwrap_err();
    assert!(matches!(
        error,
        StageError::Failure(Failure::Codec(json::CodecError {
            site: CodecSite::OrdinaryRankSize,
            ..
        }))
    ));
    assert_eq!(engine.snapshot_attempts, 0);
    assert_eq!(state.borrow().events.last(), Some(&"matrix_vector"));
}

#[derive(Default)]
struct Gate {
    needs_flush: bool,
    reads: usize,
    flushes: usize,
    output: Vec<u8>,
}
struct GatedReader {
    lines: VecDeque<Vec<u8>>,
    offset: usize,
    gate: Rc<RefCell<Gate>>,
}
impl Read for GatedReader {
    fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
        panic!("stream must use incremental BufRead");
    }
}
impl BufRead for GatedReader {
    fn fill_buf(&mut self) -> io::Result<&[u8]> {
        assert_eq!(
            LIVE_REQUESTS.with(|count| count.get()),
            0,
            "previous request still live on next read"
        );
        let mut gate = self.gate.borrow_mut();
        assert!(!gate.needs_flush, "read before reply flush");
        gate.reads += 1;
        Ok(self.lines.front().map_or(&[], |line| &line[self.offset..]))
    }
    fn consume(&mut self, count: usize) {
        let line = self.lines.front().unwrap();
        assert!(self.offset + count <= line.len());
        let ended = line[self.offset..self.offset + count].last() == Some(&b'\n');
        self.offset += count;
        if ended {
            self.gate.borrow_mut().needs_flush = true;
        }
        if self.offset == line.len() {
            self.lines.pop_front();
            self.offset = 0;
        }
    }
}
struct GatedWriter {
    gate: Rc<RefCell<Gate>>,
    fail_after: Option<usize>,
    fail_flush: bool,
}
impl Write for GatedWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let mut gate = self.gate.borrow_mut();
        if let Some(limit) = self.fail_after {
            if gate.output.len() >= limit {
                return Err(io::Error::other("selected write failure"));
            }
        }
        let count = self.fail_after.map_or(bytes.len(), |limit| {
            bytes.len().min(limit - gate.output.len())
        });
        gate.output.extend_from_slice(&bytes[..count]);
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        if self.fail_flush {
            return Err(io::Error::other("selected flush failure"));
        }
        let mut gate = self.gate.borrow_mut();
        gate.flushes += 1;
        gate.needs_flush = false;
        Ok(())
    }
}
fn gated(lines: Vec<Vec<u8>>) -> (GatedReader, GatedWriter, Rc<RefCell<Gate>>) {
    let gate = Rc::new(RefCell::new(Gate::default()));
    (
        GatedReader {
            lines: lines.into(),
            offset: 0,
            gate: gate.clone(),
        },
        GatedWriter {
            gate: gate.clone(),
            fail_after: None,
            fail_flush: false,
        },
        gate,
    )
}

/// D3: a live line replies/flushes before any read of the next line or EOF.
#[test]
fn stream_flushes_and_drops_request_before_next_read() {
    let line = wire(&envelope(request(vec![note()]), None));
    let (mut reader, mut writer, gate) = gated(vec![line.clone(), line]);
    let mut script = success_script();
    script.single("a");
    script.infer(&[0x4040_0000], &[0.25]);
    let state = Rc::new(RefCell::new(script));
    run_rank_stream(
        &mut reader,
        &mut writer,
        Factory(state.clone()),
        CachePolicy::NoCache,
    )
    .unwrap();
    let expected = format!("{{\"id\": \"x\", \"result\": {RESULT}}}\n");
    assert_eq!(gate.borrow().output, expected.repeat(2).as_bytes());
    assert_eq!(gate.borrow().flushes, 2);
    assert_eq!(gate.borrow().reads, 3);
    assert_eq!(
        state
            .borrow()
            .events
            .iter()
            .filter(|stage| **stage == "construct")
            .count(),
        1
    );
}

/// R3/D3: each backend error is terminal; no next read/retry/replacement.
#[test]
fn backend_failures_terminate_driver_without_next_read() {
    for stage in [
        "construct",
        "encode",
        "decode",
        "embed_batch_one",
        "norms_keepdims",
        "any_zero_norm",
        "divide_in_place",
        "matrix_vector",
    ] {
        let line = wire(&envelope(request(vec![note()]), None));
        let (mut reader, mut writer, gate) = gated(vec![line.clone(), line]);
        let mut script = success_script();
        script.fail = Some(stage);
        let state = Rc::new(RefCell::new(script));
        let error = run_rank_stream(
            &mut reader,
            &mut writer,
            Factory(state.clone()),
            CachePolicy::NoCache,
        )
        .unwrap_err();
        assert!(matches!(
            error.failure,
            InvocationFailure::SourceStage(Failure::Backend(_))
        ));
        assert_eq!(gate.borrow().reads, 1);
        assert_eq!(gate.borrow().flushes, 0);
        assert!(gate.borrow().output.is_empty());
        assert_eq!(state.borrow().events.last(), Some(&stage));
    }
}

/// R3/R6: restore, Source scoring and output conversion errors stop the stream.
#[test]
fn restore_scoring_and_encoding_failures_stop_before_next_read() {
    for selected in ["restore", "score", "encode"] {
        let mut script = success_script();
        let mut first_note = note();
        let cache = if selected == "restore" {
            Some(integer(1))
        } else {
            None
        };
        if selected == "score" {
            script.zero = true;
        }
        // Input json.loads accepts NaN metadata; strict output conversion fails
        // only after scoring. No independent wire renderer is used as oracle.
        let line = if selected == "encode" {
            first_note = change(&first_note, "version", Some(text("SENTINEL_NAN")));
            String::from_utf8(wire(&envelope(request(vec![first_note]), cache)))
                .unwrap()
                .replace("\"SENTINEL_NAN\"", "NaN")
                .into_bytes()
        } else {
            wire(&envelope(request(vec![first_note]), cache))
        };
        let (mut reader, mut writer, gate) = gated(vec![line.clone(), line.clone()]);
        let state = Rc::new(RefCell::new(script));
        let error = run_rank_stream(
            &mut reader,
            &mut writer,
            Factory(state.clone()),
            CachePolicy::AdvertisedCache,
        )
        .unwrap_err();
        match selected {
            "restore" => assert!(matches!(
                error.failure,
                InvocationFailure::SourceStage(Failure::Cache(_))
            )),
            "score" => assert!(matches!(
                error.failure,
                InvocationFailure::SourceStage(Failure::Source {
                    site: SourceSite::ZeroNorm,
                    ..
                })
            )),
            "encode" => assert!(matches!(
                error.failure,
                InvocationFailure::SourceStage(Failure::Codec(json::CodecError {
                    site: CodecSite::NonFinite,
                    ..
                }))
            )),
            _ => unreachable!(),
        }
        assert_eq!(error.input, line);
        assert_eq!(error.written, 0);
        assert_eq!(gate.borrow().reads, 1);
        assert_eq!(gate.borrow().flushes, 0);
        assert!(gate.borrow().output.is_empty());
        assert_eq!(
            state
                .borrow()
                .events
                .iter()
                .filter(|event| **event == "construct")
                .count(),
            1
        );
    }
}

/// R4/D1: decoded passage UTF8 conversion occurs after reencode, before next body.
#[test]
fn decoded_surrogate_refuses_at_hash_without_later_body_work() {
    let surrogate = PyString::from_codepoints(vec![0xd800]).unwrap();
    let mut script = Script::default();
    script.query(1);
    script.encode("body", SpecialTokens::Disabled, vec![7]);
    script
        .steps
        .push_back(TokenStep::Decode(vec![7], surrogate.clone()));
    script.steps.push_back(TokenStep::Encode(
        surrogate,
        SpecialTokens::Default,
        vec![8],
    ));
    let (mut engine, state) = new_engine(script);
    assert!(matches!(
        score(&mut engine, &request(vec![note(), note()])).unwrap_err(),
        StageError::Failure(Failure::PassageUtf8(_))
    ));
    assert_eq!(
        state.borrow().events,
        [
            "construct",
            "identity",
            "encode",
            "encode",
            "decode",
            "encode"
        ]
    );
    assert!(engine.cached().is_empty());
}

/// R6: integrated late projection preserves arbitrary admitted metadata values.
#[test]
fn metadata_echo_does_not_add_type_guards() {
    let metadata = object(vec![(
        "nested",
        Value::Array(vec![Value::Null, integer(7)]),
    )]);
    let note = change(
        &change(&note(), "record_id", Some(metadata.clone())),
        "version",
        Some(Value::Null),
    );
    let (mut engine, _) = new_engine(success_script());
    let result = score(&mut engine, &request(vec![note])).unwrap();
    let Value::Object(result) = result else {
        panic!()
    };
    let Some(Value::Array(scores)) = result.get(&string("scores")) else {
        panic!()
    };
    let Value::Object(score) = &scores[0] else {
        panic!()
    };
    assert_eq!(score.get(&string("record_id")), Some(&metadata));
    assert_eq!(score.get(&string("version")), Some(&Value::Null));
}

/// R3: read errors retain only selected bytes and never construct or write.
#[test]
fn read_failure_is_terminal_before_constructor() {
    struct FailingRead(bool);
    impl Read for FailingRead {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if self.0 {
                return Err(io::Error::other("selected read failure"));
            }
            self.0 = true;
            buffer[0] = b'{';
            Ok(1)
        }
    }
    let state = Rc::new(RefCell::new(Script::default()));
    let mut output = Vec::new();
    let error =
        run_rank_once(&mut FailingRead(false), &mut output, Factory(state.clone())).unwrap_err();
    assert!(matches!(
        error.failure,
        InvocationFailure::Io {
            stage: IoStage::Read,
            ..
        }
    ));
    assert_eq!(error.input, b"{");
    assert_eq!(error.written, 0);
    assert!(state.borrow().events.is_empty());
    assert!(output.is_empty());
}

/// R3/R6: partial writes and flush errors are terminal and retain exact frame.
#[test]
fn partial_write_and_flush_failure_are_distinct_from_encoding() {
    for fail_flush in [false, true] {
        let line = wire(&envelope(request(vec![note()]), None));
        let (mut reader, mut writer, gate) = gated(vec![line.clone(), line.clone()]);
        writer.fail_flush = fail_flush;
        if !fail_flush {
            writer.fail_after = Some(7);
        }
        let state = Rc::new(RefCell::new(success_script()));
        let error = run_rank_stream(
            &mut reader,
            &mut writer,
            Factory(state),
            CachePolicy::NoCache,
        )
        .unwrap_err();
        assert_eq!(error.input, line);
        assert_eq!(error.mode, InputMode::RankLine);
        assert_eq!(gate.borrow().reads, 1);
        assert!(
            matches!(error.failure,InvocationFailure::Io{stage,..} if stage==if fail_flush{IoStage::Flush}else{IoStage::Write})
        );
        if fail_flush {
            assert_eq!(error.written, gate.borrow().output.len());
            assert!(error.written > 7);
        } else {
            assert_eq!(error.written, 7);
        }
    }
}

/// R1/D3: EOF and incomplete line have different Source framing outcomes.
#[test]
fn empty_stream_and_incomplete_line_do_not_construct() {
    let line = wire(&envelope(request(vec![note()]), None));
    for bytes in [vec![], line[..line.len() - 1].to_vec()] {
        let state = Rc::new(RefCell::new(Script::default()));
        let mut output = Vec::new();
        let result = run_rank_stream(
            &mut Cursor::new(bytes.clone()),
            &mut output,
            Factory(state.clone()),
            CachePolicy::NoCache,
        );
        if bytes.is_empty() {
            assert!(result.is_ok());
        } else {
            assert!(matches!(
                result.unwrap_err().failure,
                InvocationFailure::SourceStage(Failure::Codec(json::CodecError {
                    site: CodecSite::MissingLineFeed,
                    ..
                }))
            ));
        }
        assert!(state.borrow().events.is_empty());
        assert!(output.is_empty());
    }
}

/// R3: unresolved source-depth owns the selected original bytes/profile; no ctor.
#[test]
fn pending_decode_preserves_original_input_and_profile() {
    let mut bytes = vec![b'['; 65];
    bytes.push(b'0');
    bytes.extend(vec![b']'; 65]);
    let state = Rc::new(RefCell::new(Script::default()));
    let mut output = Vec::new();
    let error = run_rank_once(
        &mut Cursor::new(bytes.clone()),
        &mut output,
        Factory(state.clone()),
    )
    .unwrap_err();
    assert!(matches!(
        error.failure,
        InvocationFailure::PendingDepth(PendingProfile::Decode(
            json::DecodeProfile::SourceBytesDefault4300
        ))
    ));
    assert_eq!(error.input, bytes);
    assert_eq!(error.mode, InputMode::RankOnce);
    assert_eq!(error.written, 0);
    assert!(state.borrow().events.is_empty());
}

/// R1/R3: rank-once stops at8MiB+1; stream atLF or8MiB+2, never drains input.
#[test]
fn bounded_read_stops_at_caps_and_first_lf() {
    let state = Rc::new(RefCell::new(Script::default()));
    let mut input = Cursor::new(vec![b' '; 8 * 1024 * 1024 + 100]);
    let error = run_rank_once(&mut input, &mut Vec::new(), Factory(state.clone())).unwrap_err();
    assert_eq!(input.position(), 8 * 1024 * 1024 + 1);
    assert_eq!(error.input.len(), 8 * 1024 * 1024 + 1);
    let mut input = Cursor::new(vec![b' '; 8 * 1024 * 1024 + 100]);
    let error = run_rank_stream(
        &mut input,
        &mut Vec::new(),
        Factory(state.clone()),
        CachePolicy::NoCache,
    )
    .unwrap_err();
    assert_eq!(input.position(), 8 * 1024 * 1024 + 2);
    assert_eq!(error.input.len(), 8 * 1024 * 1024 + 2);
    assert!(state.borrow().events.is_empty());
}
