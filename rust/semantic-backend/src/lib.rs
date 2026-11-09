//! Owning native rank composition. No CLI, routing, worker handshake or qualification.
mod arithmetic;
pub mod assets;
pub mod identity;
mod platform;

use arithmetic::{operation, tokenizer_error};
pub use arithmetic::{Normalized, Norms};
pub use assets::{AssetPaths, CleanupErrors, NativeFactory};
use cairn_semantic_math::{normalization::RawClsRow, Runtime as MathRuntime};
use cairn_semantic_ort::Session as OrtSession;
use cairn_semantic_protocol::PyString;
use cairn_semantic_rank::backend::{
    BackendError, BackendFactory, BackendSession, DecodeTokens, RawMatrix, SpecialTokens,
};
pub use cairn_semantic_rank::{run_rank_once, run_rank_stream};
use cairn_semantic_tokenizer::{BatchOneTensors, PreparedTokenizer};
use std::rc::Rc;

/// Actual loaded component ownership. Session drops before tokenizer/model owners;
/// the ORT Session retains its library Rc. No request strings are stored here.
pub struct LoadedBackend {
    ort: OrtSession,
    math: MathRuntime,
    tokenizer: PreparedTokenizer,
    owner: Rc<()>,
    report: identity::ConstructionReport,
}
impl LoadedBackend {
    pub fn observations(&self) -> &identity::ConstructionReport {
        &self.report
    }
    /// Publish usable rank identity only from complete actually observed inputs.
    /// Missing CAPI/build/CPU observations retain this loaded owner for inspection.
    pub fn initialize(self) -> Result<NativeSession, PendingInitialization> {
        match self.report.rank_identity() {
            Ok(identity) => Ok(NativeSession {
                loaded: self,
                identity,
            }),
            Err(missing) => Err(PendingInitialization {
                loaded: self,
                missing,
            }),
        }
    }
    pub fn encode(&self, text: &PyString, policy: SpecialTokens) -> Result<Vec<u32>, BackendError> {
        self.tokenizer
            .rank_encode(text, policy)
            .map_err(|e| tokenizer_error("rank encode", e))
    }
    pub fn decode(&self, tokens: &[u32], policy: DecodeTokens) -> Result<PyString, BackendError> {
        self.tokenizer
            .rank_decode(tokens, policy)
            .map_err(|e| tokenizer_error("rank decode", e))
    }
    pub fn embed_batch_one(&mut self, texts: &[PyString]) -> Result<RawMatrix, BackendError> {
        let mut bits = Vec::new();
        for text in texts {
            // Tokenize then infer then normalize EACH text, query/miss iterator order.
            let encoding = self
                .tokenizer
                .model_encoding(text)
                .map_err(|e| tokenizer_error("model encoding", e))?;
            let tensors = BatchOneTensors::from_encoding(&encoding)
                .map_err(|e| tokenizer_error("batch-one tensors", e))?;
            let raw = self
                .ort
                .raw_cls(tensors)
                .map_err(|e| operation("ORT raw CLS", e))?;
            let first = RawClsRow::from_values(raw.0.into_iter().map(f32::from_bits).collect())
                .map_err(|e| operation("raw CLS row", e))?
                .normalize();
            bits.extend(first.values().iter().map(|v| v.to_bits()));
        }
        Ok(RawMatrix {
            rows: texts.len(),
            columns: 384,
            bits,
        })
    }
    pub fn norms_keepdims(&self, vectors: &RawMatrix) -> Result<Norms, BackendError> {
        arithmetic::norms(vectors, &self.owner)
    }
    pub fn any_zero_norm(&self, norms: &Norms) -> Result<bool, BackendError> {
        arithmetic::any_zero(norms, &self.owner)
    }
    pub fn divide_in_place(
        &self,
        vectors: RawMatrix,
        norms: Norms,
    ) -> Result<Normalized, BackendError> {
        arithmetic::divide(vectors, norms, &self.owner)
    }
    pub fn matrix_vector(&mut self, normalized: Normalized) -> Result<Vec<f32>, BackendError> {
        if !Rc::ptr_eq(&self.owner, &normalized.owner) {
            return Err(operation("matvec owner", "different backend session"));
        }
        self.math
            .matrix_vector(normalized.matrix)
            .map_err(|e| operation("native matvec", e))
    }
}
pub struct PendingInitialization {
    loaded: LoadedBackend,
    missing: Vec<identity::MissingObservation>,
}
impl PendingInitialization {
    pub fn missing(&self) -> &[identity::MissingObservation] {
        &self.missing
    }
    pub fn loaded(&self) -> &LoadedBackend {
        &self.loaded
    }
    pub fn into_loaded(self) -> LoadedBackend {
        self.loaded
    }
}
pub struct NativeSession {
    loaded: LoadedBackend,
    identity: PyString,
}
impl BackendSession for NativeSession {
    type Norms = Norms;
    type Normalized = Normalized;
    fn identity(&self) -> &PyString {
        &self.identity
    }
    fn encode(
        &mut self,
        text: &PyString,
        specials: SpecialTokens,
    ) -> Result<Vec<u32>, BackendError> {
        self.loaded.encode(text, specials)
    }
    fn decode(&mut self, tokens: &[u32], policy: DecodeTokens) -> Result<PyString, BackendError> {
        self.loaded.decode(tokens, policy)
    }
    fn embed_batch_one(&mut self, texts: &[PyString]) -> Result<RawMatrix, BackendError> {
        self.loaded.embed_batch_one(texts)
    }
    fn norms_keepdims(&mut self, vectors: &RawMatrix) -> Result<Norms, BackendError> {
        self.loaded.norms_keepdims(vectors)
    }
    fn any_zero_norm(&mut self, norms: &Norms) -> Result<bool, BackendError> {
        self.loaded.any_zero_norm(norms)
    }
    fn divide_in_place(
        &mut self,
        vectors: RawMatrix,
        norms: Norms,
    ) -> Result<Normalized, BackendError> {
        self.loaded.divide_in_place(vectors, norms)
    }
    fn matrix_vector(&mut self, vectors: Normalized) -> Result<Vec<f32>, BackendError> {
        self.loaded.matrix_vector(vectors)
    }
}
impl BackendFactory for NativeFactory {
    type Session = NativeSession;
    fn construct(&mut self) -> Result<NativeSession, BackendError> {
        let loaded = self.load()?;
        loaded.initialize().map_err(|pending| {
            operation("unresolved initialization observations", pending.missing())
        })
    }
}

#[cfg(test)]
mod tests;
