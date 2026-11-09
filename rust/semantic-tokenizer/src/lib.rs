//! Real prepared tokenizer and batch-one input tensors; no ORT/model/worker.
pub mod prepared;
pub mod tensors;

pub use prepared::{ConsumedFile, PreparationNotice, PreparedInputs, PreparedTokenizer};
pub use tensors::BatchOneTensors;

use cairn_semantic_protocol::{FailureKind, Utf8Error};

#[derive(Debug)]
pub enum Error {
    Json {
        file: &'static str,
        source: serde_json::Error,
    },
    Configuration {
        file: &'static str,
        field: String,
        detail: &'static str,
    },
    Tokenizer {
        stage: &'static str,
        source: tokenizers::Error,
    },
    TextUtf8(Utf8Error),
    EncodingLengths {
        ids: usize,
        mask: usize,
    },
    EncodingBatchCount(usize),
}

impl Error {
    /// The established protocol family applies to a lone-surrogate text bridge.
    /// Library/config errors retain their own stage/source instead of inventing
    /// a Python exception or claiming model/runtime qualification.
    pub fn source_kind(&self) -> Option<FailureKind> {
        match self {
            Self::TextUtf8(_) => Some(FailureKind::UnicodeEncodeError),
            _ => None,
        }
    }
    pub(crate) fn tokenizer(stage: &'static str, source: tokenizers::Error) -> Self {
        Self::Tokenizer { stage, source }
    }
}

#[cfg(test)]
mod tests;
