//! Original-text query projection only: no model, identity or readiness claim.
use crate::{offsets::OriginalText, prepared::utf8, Error, PreparedTokenizer};
use cairn_semantic_protocol::{FailureKind, PyString};
use sha2::{Digest, Sha256};

const PREFIX: &str = "Represent this sentence for searching relevant passages: ";

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QueryProjectionMetadata {
    pub method: &'static str,
    pub truncated: bool,
    pub original_tokens: usize,
    pub embedded_tokens: usize,
    pub prefix_bytes: usize,
    pub prefix_sha256: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QueryProjection {
    pub question: String,
    pub metadata: QueryProjectionMetadata,
}

#[derive(Debug)]
pub enum QueryPrefixError {
    InputBounds,
    NoBoundedOriginalPrefix,
    TokenBound,
    Encoding(Error),
}

impl QueryPrefixError {
    pub fn source_kind(&self) -> Option<FailureKind> {
        match self {
            Self::Encoding(error) => error.source_kind(),
            _ => Some(FailureKind::ValueError),
        }
    }
}

impl PreparedTokenizer {
    /// Mirrors embedding_worker.query_prefix using default specials on the
    /// rank clone. Model truncation/padding must not determine these counts.
    pub fn query_prefix(&self, text: &PyString) -> Result<QueryProjection, QueryPrefixError> {
        project_query(text, |question| {
            let encoding = self.rank_char_encoding(question, true)?;
            Ok(ProjectionEncoding {
                count: encoding.get_ids().len(),
                offsets: encoding.get_offsets().to_vec(),
            })
        })
    }
}

// Owned observations only. Scripted test collaborators remain private and run
// this same algorithm; they cannot manufacture a prepared/ready backend.
struct ProjectionEncoding {
    count: usize,
    offsets: Vec<(usize, usize)>,
}

fn python_slice_end(length: usize, end: i128) -> usize {
    let length = length as i128;
    (if end < 0 { length + end } else { end }).clamp(0, length) as usize
}

fn project_query(
    text: &PyString,
    mut encode: impl FnMut(&str) -> Result<ProjectionEncoding, Error>,
) -> Result<QueryProjection, QueryPrefixError> {
    if text.python_blank() {
        return Err(QueryPrefixError::InputBounds);
    }
    let text = utf8(text).map_err(QueryPrefixError::Encoding)?;
    if text.len() > 4096 {
        return Err(QueryPrefixError::InputBounds);
    }
    let original = OriginalText::new(&text);
    let question = format!("{PREFIX}{text}");
    let encoding = encode(&question).map_err(QueryPrefixError::Encoding)?;
    let original_tokens = encoding.count;
    let mut embedded_tokens = original_tokens;
    let mut prefix = text.as_str();
    if original_tokens > 512 {
        // i128 retains Python's signed slice-end arithmetic for the eight
        // retreats even when an observed count spans the full 64-bit usize.
        let mut end = 511i128;
        let mut accepted = false;
        for _ in 0..8 {
            let stop = encoding.offsets[..python_slice_end(encoding.offsets.len(), end)]
                .iter()
                .filter(|(first, stop)| stop > first)
                .map(|(_, stop)| *stop)
                .max()
                .unwrap_or(0);
            let length = stop
                .checked_sub(PREFIX.chars().count())
                .filter(|length| *length > 0 && *length < original.char_len())
                .ok_or(QueryPrefixError::NoBoundedOriginalPrefix)?;
            prefix = original
                .slice(0..length)
                .ok_or(QueryPrefixError::NoBoundedOriginalPrefix)?;
            embedded_tokens = encode(&format!("{PREFIX}{prefix}"))
                .map_err(QueryPrefixError::Encoding)?
                .count;
            if !PyString::from(prefix).python_blank() && embedded_tokens <= 512 {
                accepted = true;
                break;
            }
            end -= embedded_tokens.saturating_sub(512).max(1) as i128;
        }
        if !accepted {
            return Err(QueryPrefixError::TokenBound);
        }
    }
    let prefix_sha256 = Sha256::digest(prefix.as_bytes())
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect();
    Ok(QueryProjection {
        question: format!("{PREFIX}{prefix}"),
        metadata: QueryProjectionMetadata {
            method: "original-prefix/1",
            truncated: prefix != text.as_str(),
            original_tokens,
            embedded_tokens,
            prefix_bytes: prefix.len(),
            prefix_sha256,
        },
    })
}

#[cfg(test)]
mod tests;
