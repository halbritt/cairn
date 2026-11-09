//! New adapter checks. Synthetic bit payloads are admitted cache inputs, NOT
//! real ORT or OpenBLAS results. Only the standalone normalization C path runs.
use super::*;
use cairn_semantic_rank::backend::RawMatrix;
use std::rc::Rc;
fn matrix(rows: &[u32]) -> RawMatrix {
    let mut bits = Vec::new();
    for &value in rows {
        let mut row = vec![0; 384];
        row[0] = value;
        bits.extend(row);
    }
    RawMatrix {
        rows: rows.len(),
        columns: 384,
        bits,
    }
}
#[test]
fn backend1_restored_first_bits_are_not_first_renormalized_or_given_fake_norms() {
    let bits = matrix(&[0x3a80_0000, 0x3a00_0000, 0x3a00_0000]);
    let copied = bits.clone();
    let owner = Rc::new(());
    let pending = arithmetic::norms(&bits, &owner).unwrap();
    assert!(!arithmetic::any_zero(&pending, &owner).unwrap());
    // Inbound arbitrary first-pass magnitudes are preserved until the SECOND
    // norm, without constructing a performed FirstNormalizedRow.
    let normalized = arithmetic::divide(bits, pending, &owner).unwrap();
    assert_eq!(normalized.matrix.query()[0].to_bits(), 1.0f32.to_bits());
    assert_eq!(normalized.matrix.passages()[0].to_bits(), 1.0f32.to_bits());
    assert_eq!(copied.bits[0], 0x3a80_0000);
    assert_eq!(copied.bits[384], 0x3a00_0000);
    assert_eq!(copied.bits[768], 0x3a00_0000);
}
#[test]
fn backend1_norms_bind_exact_bits_shape_and_session_before_division() {
    let original = matrix(&[0x3f80_0000, 0x3f80_0000]);
    let owner = Rc::new(());
    let other = Rc::new(());
    let norm = arithmetic::norms(&original, &owner).unwrap();
    assert!(arithmetic::any_zero(&norm, &other).is_err());
    assert!(arithmetic::divide(original.clone(), norm, &other).is_err());
    let norm = arithmetic::norms(&original, &owner).unwrap();
    let mut changed = original.clone();
    changed.bits[1] = 0x8000_0000;
    assert!(arithmetic::divide(changed, norm, &owner).is_err());
    let norm = arithmetic::norms(&original, &owner).unwrap();
    let mut changed = original.clone();
    changed.rows = 1;
    assert!(arithmetic::divide(changed, norm, &owner).is_err());
}
#[test]
fn backend1_zero_norm_failure_preserves_all_source_cache_bits() {
    let original = matrix(&[0x3a80_0000, 0, 0x3f80_0000]);
    let owner = Rc::new(());
    let cached = original.clone();
    let norms = arithmetic::norms(&original, &owner).unwrap();
    assert!(arithmetic::any_zero(&norms, &owner).unwrap());
    assert!(arithmetic::divide(original.clone(), norms, &owner).is_err());
    assert_eq!(original, cached);
}
#[test]
fn backend1_wrong_storage_and_missing_query_fail_before_native_normalization() {
    for input in [
        RawMatrix {
            rows: 0,
            columns: 384,
            bits: vec![],
        },
        RawMatrix {
            rows: 1,
            columns: 383,
            bits: vec![0; 384],
        },
        RawMatrix {
            rows: 2,
            columns: 384,
            bits: vec![0; 384],
        },
    ] {
        assert!(arithmetic::norms(&input, &Rc::new(())).is_err());
    }
}
#[test]
fn backend1_bit_admission_itself_does_not_add_numeric_policy() {
    use cairn_semantic_math::normalization::FirstPassOccurrences;
    let special = [0, 0x8000_0000, 0x7fc0_1234, 0x7f80_0000, 0xff80_0000, 1];
    let mut bits = vec![0; 384];
    bits[..special.len()].copy_from_slice(&special);
    // Format-only boundary; rank/cache owners still apply their original checks.
    let admitted = FirstPassOccurrences::from_rank_first_pass_bits(1, 384, bits).unwrap();
    assert_eq!(
        admitted.values()[..special.len()]
            .iter()
            .map(|x| x.to_bits())
            .collect::<Vec<_>>(),
        special
    );
}
#[test]
fn backend1_asset_relative_path_refuses_before_any_native_load() {
    assert!(assets::read_owned(std::path::Path::new("relative/model.onnx")).is_err());
}

// Literal collaborators exist ONLY in this cfg(test) module. They exercise the
// unchanged rank stream and NEW real bit-admission/normalization adapters, not
// real tokenization, ORT inference, OpenBLAS or worker qualification.
use cairn_semantic_protocol::{CachePolicy, PyString};
use cairn_semantic_rank::backend::{
    BackendError, BackendFactory, BackendSession, DecodeTokens, SpecialTokens,
};
use std::{cell::RefCell, io::Cursor};
#[derive(Default)]
struct LiteralTrace {
    model_inputs: Vec<Vec<PyString>>,
    occurrences: Vec<RawMatrix>,
}
struct LiteralFactory(Rc<RefCell<LiteralTrace>>);
struct LiteralSession {
    trace: Rc<RefCell<LiteralTrace>>,
    owner: Rc<()>,
    identity: PyString,
}
impl BackendFactory for LiteralFactory {
    type Session = LiteralSession;
    fn construct(&mut self) -> Result<Self::Session, BackendError> {
        Ok(LiteralSession {
            trace: Rc::clone(&self.0),
            owner: Rc::new(()),
            identity: py("synthetic-backend1-only"),
        })
    }
}
fn py(s: &str) -> PyString {
    PyString::from_codepoints(s.chars().map(u32::from).collect()).unwrap()
}
impl BackendSession for LiteralSession {
    type Norms = Norms;
    type Normalized = Normalized;
    fn identity(&self) -> &PyString {
        &self.identity
    }
    fn encode(&mut self, _: &PyString, _: SpecialTokens) -> Result<Vec<u32>, BackendError> {
        Ok(vec![7])
    }
    fn decode(&mut self, _: &[u32], _: DecodeTokens) -> Result<PyString, BackendError> {
        Ok(py("chunk"))
    }
    fn embed_batch_one(&mut self, texts: &[PyString]) -> Result<RawMatrix, BackendError> {
        self.trace.borrow_mut().model_inputs.push(texts.to_vec());
        // Literal FIRST-pass bits: query is 2^-10 and each unique miss 2^-11.
        // They are deliberately not unit length, exposing unwanted first renorm.
        Ok(matrix(
            &(0..texts.len())
                .map(|i| if i == 0 { 0x3a80_0000 } else { 0x3a00_0000 })
                .collect::<Vec<_>>(),
        ))
    }
    fn norms_keepdims(&mut self, v: &RawMatrix) -> Result<Norms, BackendError> {
        self.trace.borrow_mut().occurrences.push(v.clone());
        arithmetic::norms(v, &self.owner)
    }
    fn any_zero_norm(&mut self, n: &Norms) -> Result<bool, BackendError> {
        arithmetic::any_zero(n, &self.owner)
    }
    fn divide_in_place(&mut self, v: RawMatrix, n: Norms) -> Result<Normalized, BackendError> {
        arithmetic::divide(v, n, &self.owner)
    }
    fn matrix_vector(&mut self, n: Normalized) -> Result<Vec<f32>, BackendError> {
        // A literal score collaborator, explicitly NOT a BLAS implementation.
        assert!(Rc::ptr_eq(&self.owner, &n.owner));
        Ok(vec![0.25; n.matrix.layout().passage_rows])
    }
}
fn stream_request(id: &str, version: bool) -> Vec<u8> {
    let note = if version {
        serde_json::json!({"body":"body","body_sha256":"230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5","record_id":"r","version":1})
    } else {
        serde_json::json!({"body":"body","body_sha256":"230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5","record_id":"r"})
    };
    let mut bytes = serde_json::to_vec(
        &serde_json::json!({"id":id,"request":{"query":"q","notes":[note.clone(),note]}}),
    )
    .unwrap();
    bytes.push(b'\n');
    bytes
}
#[test]
fn backend1_stream_query_unique_misses_and_full_warm_duplicates_use_new_adapter() {
    let trace = Rc::new(RefCell::new(LiteralTrace::default()));
    let mut bytes = stream_request("cold", true);
    bytes.extend(stream_request("warm", true));
    let mut input = Cursor::new(bytes);
    let mut output = Vec::new();
    run_rank_stream(
        &mut input,
        &mut output,
        LiteralFactory(Rc::clone(&trace)),
        CachePolicy::NoCache,
    )
    .unwrap();
    let trace = trace.borrow();
    assert_eq!(trace.model_inputs.len(), 2);
    assert_eq!(
        trace.model_inputs[0],
        vec![
            py("Represent this sentence for searching relevant passages: q"),
            py("chunk")
        ]
    );
    assert_eq!(
        trace.model_inputs[1],
        vec![py(
            "Represent this sentence for searching relevant passages: q"
        )]
    );
    assert_eq!(
        trace.occurrences,
        vec![matrix(&[0x3a80_0000, 0x3a00_0000, 0x3a00_0000]); 2]
    );
    assert_eq!(output.iter().filter(|&&c| c == b'\n').count(), 2);
}
#[test]
fn backend1_stream_metadata_failure_writes_no_replacement_cache_or_next_reply() {
    let trace = Rc::new(RefCell::new(LiteralTrace::default()));
    let mut bytes = stream_request("first", true);
    bytes.extend(stream_request("bad_metadata", false));
    let third = stream_request("must_not_read", true);
    bytes.extend_from_slice(&third);
    let mut input = Cursor::new(bytes);
    let mut output = Vec::new();
    let result = run_rank_stream(
        &mut input,
        &mut output,
        LiteralFactory(Rc::clone(&trace)),
        CachePolicy::AdvertisedCache,
    );
    assert!(result.is_err());
    assert_eq!(output.iter().filter(|&&c| c == b'\n').count(), 1);
    let first: serde_json::Value = serde_json::from_slice(&output).unwrap();
    assert_eq!(first["id"], "first");
    assert!(first.get("cache").is_some());
    let remaining = &input.get_ref()[input.position() as usize..];
    assert_eq!(remaining, third.as_slice());
    let trace = trace.borrow();
    assert_eq!(trace.model_inputs.len(), 2);
    assert_eq!(trace.model_inputs[1].len(), 1);
    assert_eq!(
        trace.occurrences[1],
        matrix(&[0x3a80_0000, 0x3a00_0000, 0x3a00_0000])
    );
    // A prior successful frame remains intact; no failed-frame snapshot/reply
    // or read of the third frame occurs. Rank still owns atomic cache replacement.
}

#[test]
fn backend1_text_bridge_retains_its_family_without_broad_native_conversion() {
    let source = cairn_semantic_protocol::Utf8Error {
        index: 2,
        codepoint: 0xd800,
    };
    let failure = arithmetic::tokenizer_error(
        "rank encode",
        cairn_semantic_tokenizer::Error::TextUtf8(source),
    );
    assert_eq!(
        failure.source_kind(),
        Some(cairn_semantic_protocol::FailureKind::UnicodeEncodeError)
    );
    assert!(matches!(failure,BackendError::TextUtf8 { source: actual, .. } if actual==source));
    assert_eq!(
        arithmetic::operation("native", "unclassified native failure").source_kind(),
        None
    );
}
