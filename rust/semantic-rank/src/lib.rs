//! Integrated source rank/stream core; no executable/native backend is shipped.
//! cfg(test) doubles and component results prove source control flow only.

pub mod backend;
mod rank;
mod stream;

pub use stream::{run_rank_once, run_rank_stream};

use cairn_semantic_protocol::{
    cache, json, FailureKind, IrError, ProjectionFailure, Refusal, Utf8Error,
};
use std::io;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SourceSite {
    QueryModelTokens,
    BodyModelTokens(usize),
    OccurrenceBudget,
    PassageModelTokens,
    RawModelShapeOrFinite,
    ZeroNorm,
}

#[derive(Debug)]
pub enum Failure {
    Codec(json::CodecError),
    Prefix(Refusal),
    Projection(ProjectionFailure),
    Cache(cache::CacheRefusal),
    Backend(backend::BackendError),
    Source { site: SourceSite, kind: FailureKind },
    PassageUtf8(Utf8Error),
    Ir(IrError),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PendingProfile {
    Decode(json::DecodeProfile),
    Render(json::RenderProfile),
}

#[derive(Debug)]
pub(crate) enum StageError {
    Failure(Failure),
    Pending(PendingProfile),
}

impl From<Failure> for StageError {
    fn from(error: Failure) -> Self {
        Self::Failure(error)
    }
}
impl From<backend::BackendError> for StageError {
    fn from(error: backend::BackendError) -> Self {
        Self::Failure(Failure::Backend(error))
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IoStage {
    Read,
    Write,
    Flush,
}

#[derive(Debug)]
pub enum InvocationFailure {
    SourceStage(Failure),
    PendingDepth(PendingProfile),
    Io { stage: IoStage, error: io::Error },
}

/// Owns the selected original input/profile on failure or pending. Partial
/// writes are distinguished from conversion failures and successful completion.
#[derive(Debug)]
pub struct InvocationError {
    pub failure: InvocationFailure,
    pub input: Vec<u8>,
    pub mode: json::InputMode,
    pub written: usize,
}

#[cfg(test)]
thread_local! { static LIVE_REQUESTS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) }; }

pub(crate) struct RequestScope;
impl RequestScope {
    pub(crate) fn new() -> Self {
        #[cfg(test)]
        LIVE_REQUESTS.with(|count| count.set(count.get() + 1));
        Self
    }
}
impl Drop for RequestScope {
    fn drop(&mut self) {
        #[cfg(test)]
        LIVE_REQUESTS.with(|count| count.set(count.get() - 1));
    }
}

#[cfg(test)]
mod tests;
