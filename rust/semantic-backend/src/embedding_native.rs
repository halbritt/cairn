//! Actual native adapter for embedding; execution needs separate admission.
use crate::embedding_response::{EmbeddingFactory, EmbeddingSession, FirstNormalizedRow};
use crate::{arithmetic::operation, identity::ConstructionReport, LoadedBackend, NativeFactory};
use cairn_semantic_protocol::PyString;
use cairn_semantic_rank::backend::{BackendError, RawMatrix};
use cairn_semantic_tokenizer::PreparedTokenizer;

/// Owns the same loaded session, tokenizer, libraries and observation report.
/// Fields are private; only the real factory can construct this adapter.
pub struct NativeEmbeddingSession {
    loaded: LoadedBackend,
}

impl EmbeddingFactory for NativeFactory {
    type Session = NativeEmbeddingSession;
    fn construct(self) -> Result<Self::Session, BackendError> {
        let loaded = self.load()?;
        require_embedding_observations(loaded.observations())?;
        Ok(NativeEmbeddingSession { loaded })
    }
}

pub(crate) fn require_embedding_observations(
    report: &ConstructionReport,
) -> Result<(), BackendError> {
    report
        .embedding_identity()
        .map(|_| ())
        .map_err(|missing| operation("unresolved embedding initialization observations", missing))
}

impl EmbeddingSession for NativeEmbeddingSession {
    fn observations(&self) -> &ConstructionReport {
        self.loaded.observations()
    }
    fn tokenizer(&self) -> &PreparedTokenizer {
        &self.loaded.tokenizer
    }
    fn embed_first_normalized(
        &mut self,
        texts: &[String],
    ) -> Result<Vec<FirstNormalizedRow>, BackendError> {
        let texts: Vec<_> = texts.iter().map(|s| PyString::from(s.as_str())).collect();
        // Existing model_encoding -> raw CLS -> FIRST normalization only.
        // No rank full-occurrence norm/divide/matvec is reached on this path.
        first_rows(self.loaded.embed_batch_one(&texts)?)
    }
}

// Structural conversion only: the landed response loop retains the complete
// batch-count and all-row finite/nonzero validation before JSON conversion.
pub(crate) fn first_rows(matrix: RawMatrix) -> Result<Vec<FirstNormalizedRow>, BackendError> {
    if matrix.columns != 384 || matrix.rows.checked_mul(384) != Some(matrix.bits.len()) {
        return Err(operation(
            "embedding first-pass storage",
            "invalid matrix layout",
        ));
    }
    Ok(matrix
        .bits
        .chunks_exact(384)
        .map(|bits| FirstNormalizedRow {
            shape: vec![384],
            bits: bits.to_vec(),
        })
        .collect())
}
