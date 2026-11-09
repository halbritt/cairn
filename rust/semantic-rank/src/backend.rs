//! Source-only backend seam. There is deliberately no production implementation.
//! A future native factory must validate complete assets/manifest, loaded runtime,
//! tokenizer settings and rank arithmetic before returning an owning session.
//! Trait success alone is not an initialized-worker qualification attestation.

use cairn_semantic_protocol::PyString;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SpecialTokens {
    Default,
    Disabled,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DecodeTokens {
    KeepSpecialTokens,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BackendError {
    Unavailable(String),
    Operation { stage: &'static str, detail: String },
}

/// Explicit supplied float32 bits and declared two-dimensional shape. The core
/// checks model shape/finite bits, then reconstructs ALL occurrence rows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RawMatrix {
    pub rows: usize,
    pub columns: usize,
    pub bits: Vec<u32>,
}

pub trait BackendFactory {
    type Session: BackendSession;
    /// Must construct a real owning backend for production; no ready boolean,
    /// supplied identity shortcut, replacement, retry or fallback is provided.
    fn construct(&mut self) -> Result<Self::Session, BackendError>;
}

pub trait BackendSession {
    type Norms;
    type Normalized;
    fn identity(&self) -> &PyString;
    fn encode(
        &mut self,
        text: &PyString,
        specials: SpecialTokens,
    ) -> Result<Vec<u32>, BackendError>;
    fn decode(&mut self, tokens: &[u32], policy: DecodeTokens) -> Result<PyString, BackendError>;
    /// Exactly [prefixed query] + first-occurrence unique misses, batch_size=1.
    fn embed_batch_one(&mut self, texts: &[PyString]) -> Result<RawMatrix, BackendError>;
    /// Backend obligation: NumPy-equivalent linalg.norm(axis=1,keepdims=True)
    /// over the full reconstructed raw matrix, preserving dtype/operation order.
    /// No scalar/per-row/f64 substitute arithmetic is implemented by this core.
    fn norms_keepdims(&mut self, vectors: &RawMatrix) -> Result<Self::Norms, BackendError>;
    fn any_zero_norm(&mut self, norms: &Self::Norms) -> Result<bool, BackendError>;
    fn divide_in_place(
        &mut self,
        vectors: RawMatrix,
        norms: Self::Norms,
    ) -> Result<Self::Normalized, BackendError>;
    /// Equivalent to vectors[1:] @ vectors[0], in occurrence order. Its output
    /// is NOT given new finiteness/refusal guards; reduction uses published code.
    fn matrix_vector(&mut self, vectors: Self::Normalized) -> Result<Vec<f32>, BackendError>;
}
