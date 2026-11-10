//! Item6 adapter-only fixtures. No NativeFactory::load/construct or native call.
use crate::embedding_native::{first_rows, require_embedding_observations};
use cairn_semantic_rank::backend::{BackendError, RawMatrix};

#[test]
fn embedding_entry6_native_rows_retain_order_and_first_pass_bits() {
    let mut bits = vec![0; 768];
    let special = [
        0x3a800000, 0x80000000, 0x7fc01234, 0x7f800000, 0xff800000, 1,
    ];
    bits[..special.len()].copy_from_slice(&special);
    bits[384] = 0x3a000000;
    let rows = first_rows(RawMatrix {
        rows: 2,
        columns: 384,
        bits,
    })
    .unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0].shape, [384]);
    assert_eq!(rows[1].shape, [384]);
    assert_eq!(&rows[0].bits[..special.len()], &special);
    assert_eq!(rows[1].bits[0], 0x3a000000);
    assert!(rows[0].bits[special.len()..].iter().all(|b| *b == 0));
    assert!(rows[1].bits[1..].iter().all(|b| *b == 0));
}

#[test]
fn embedding_entry6_native_storage_mismatch_refuses_without_numeric_fallback() {
    for matrix in [
        RawMatrix {
            rows: 1,
            columns: 383,
            bits: vec![0; 383],
        },
        RawMatrix {
            rows: 2,
            columns: 384,
            bits: vec![0; 384],
        },
        RawMatrix {
            rows: usize::MAX,
            columns: 384,
            bits: vec![],
        },
    ] {
        assert!(matches!(
            first_rows(matrix).unwrap_err(),
            BackendError::Operation {
                stage: "embedding first-pass storage",
                ..
            }
        ));
    }
}

#[test]
fn embedding_entry6_empty_native_batch_adds_no_phantom_row() {
    assert!(first_rows(RawMatrix {
        rows: 0,
        columns: 384,
        bits: vec![]
    })
    .unwrap()
    .is_empty());
}

#[test]
fn embedding_entry6_native_guard_accepts_complete_owned_report_only() {
    let report = super::fixture();
    require_embedding_observations(&report).unwrap();
    // Only the guard ran: no NativeEmbeddingSession is made from fixture data.
    assert_eq!(
        report.embedding_identity().unwrap(),
        cairn_semantic_protocol::PyString::from(super::EMBED_DIGEST)
    );
}

#[test]
fn embedding_init7_native_guard_allows_each_named_unobserved_global() {
    for field in ["effective_provider", "cpu_arena", "remaining_defaults"] {
        let mut report = super::fixture();
        report.inputs["session"][field] = serde_json::Value::Null;
        require_embedding_observations(&report).unwrap();
        assert_eq!(
            report.embedding_manifest().unwrap()["unobserved_observations"],
            serde_json::json!([format!("session.{field}")])
        );
    }
}

#[test]
fn embedding_entry6_native_guard_retains_cached_missing_observation() {
    let mut report = super::fixture();
    report.missing.push(super::missing(
        "fixture-pending",
        "requires actual observation",
    ));
    let error = require_embedding_observations(&report).unwrap_err();
    assert_eq!(error.source_kind(), None);
    let BackendError::Operation { detail, .. } = error else {
        panic!("expected guarded refusal")
    };
    assert!(detail.contains("fixture-pending"));
    assert!(detail.contains("requires actual observation"));
}

use crate::embedding_response::{
    run_embedding, EmbeddingFactory, EmbeddingSession, Failure, FirstNormalizedRow,
};
use std::{
    cell::Cell,
    io::{self, BufRead, Read, Write},
    rc::Rc,
};

struct NeverSession;
impl EmbeddingSession for NeverSession {
    fn observations(&self) -> &super::ConstructionReport {
        panic!("failed initialization produced a session")
    }
    fn tokenizer(&self) -> &cairn_semantic_tokenizer::PreparedTokenizer {
        panic!("failed initialization reached tokenizer")
    }
    fn embed_first_normalized(
        &mut self,
        _: &[String],
    ) -> Result<Vec<FirstNormalizedRow>, BackendError> {
        panic!("failed initialization reached model")
    }
}
struct RefusingFactory {
    report: super::ConstructionReport,
    dropped: Rc<Cell<bool>>,
}
impl Drop for RefusingFactory {
    fn drop(&mut self) {
        self.dropped.set(true);
    }
}
impl EmbeddingFactory for RefusingFactory {
    type Session = NeverSession;
    fn construct(self) -> Result<NeverSession, BackendError> {
        require_embedding_observations(&self.report)?;
        panic!("invalid observation guard was bypassed")
    }
}
struct NoIo;
impl Read for NoIo {
    fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
        panic!("input read before initialization")
    }
}
impl BufRead for NoIo {
    fn fill_buf(&mut self) -> io::Result<&[u8]> {
        panic!("input fill before initialization")
    }
    fn consume(&mut self, _: usize) {
        panic!("input consumed before initialization")
    }
}
impl Write for NoIo {
    fn write(&mut self, _: &[u8]) -> io::Result<usize> {
        panic!("output write after failed initialization")
    }
    fn flush(&mut self) -> io::Result<()> {
        panic!("output flush after failed initialization")
    }
}

#[test]
fn embedding_init7_invalid_observation_gate_precedes_loop_io_and_drops_factory() {
    let mut report = super::fixture();
    report.inputs["session"]["cpu_arena"] = serde_json::json!("invalid observed boolean");
    let dropped = Rc::new(Cell::new(false));
    let error = run_embedding(
        &mut NoIo,
        &mut NoIo,
        RefusingFactory {
            report,
            dropped: dropped.clone(),
        },
    )
    .unwrap_err();
    assert!(dropped.get());
    assert!(error.input.is_empty());
    assert_eq!(error.written, 0);
    assert!(matches!(
        error.failure,
        Failure::Backend(BackendError::Operation {
            stage: "unresolved embedding initialization observations",
            ..
        })
    ));
}
