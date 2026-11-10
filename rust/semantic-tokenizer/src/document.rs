//! Original-text document projection, without vectors, identity or readiness.
use crate::{offsets::OriginalText, prepared::utf8, Error, PreparedTokenizer};
use cairn_semantic_protocol::{FailureKind, PyString};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Utf8Span {
    pub offset: usize,
    pub length: usize,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DocumentPassage {
    pub text: String,
    pub span: Utf8Span,
}

#[derive(Debug)]
pub enum DocumentSpansError {
    InputBounds,
    NoModelTokens,
    InvalidOffsets,
    PassageBounds,
    PassageTokenBound,
    Encoding(Error),
}

impl DocumentSpansError {
    pub fn source_kind(&self) -> Option<FailureKind> {
        match self {
            Self::Encoding(error) => error.source_kind(),
            _ => Some(FailureKind::ValueError),
        }
    }
}

impl PreparedTokenizer {
    /// Mirrors source_passages plus the document branch's input, passage-count
    /// and re-tokenization guards. Every guard completes before returning data.
    pub fn document_passages(
        &self,
        body: &PyString,
    ) -> Result<Vec<DocumentPassage>, DocumentSpansError> {
        project_document(body, |text, special_tokens| {
            let encoding = self.rank_char_encoding(text, special_tokens)?;
            Ok(DocumentEncoding {
                count: encoding.get_ids().len(),
                offsets: encoding.get_offsets().to_vec(),
            })
        })
    }
}

// Private owned observations permit difficult offset/retokenization fixtures
// through the same algorithm; no alternate production backend is exposed.
struct DocumentEncoding {
    count: usize,
    offsets: Vec<(usize, usize)>,
}

fn window_shape(tokens: usize) -> (usize, usize) {
    // ceil(tokens / 256) without overflowing tokens + 255. For any supported
    // usize width, ceil(usize::MAX / 256) + 32 fits as well.
    let stride = (tokens / 256 + usize::from(tokens % 256 != 0)).max(96);
    (stride, (stride + 32).max(128))
}

fn project_document(
    body: &PyString,
    mut encode: impl FnMut(&str, bool) -> Result<DocumentEncoding, Error>,
) -> Result<Vec<DocumentPassage>, DocumentSpansError> {
    if body.python_blank() {
        return Err(DocumentSpansError::InputBounds);
    }
    let body = utf8(body).map_err(DocumentSpansError::Encoding)?;
    if body.len() > 65536 {
        return Err(DocumentSpansError::InputBounds);
    }
    let original = OriginalText::new(&body);
    let encoding = encode(&body, false).map_err(DocumentSpansError::Encoding)?;
    let n = encoding.offsets.len();
    if n == 0 {
        return Err(DocumentSpansError::NoModelTokens);
    }
    let (stride, window) = window_shape(n);
    let mut passages = Vec::new();
    for start in (0..n).step_by(stride) {
        // Equivalent to min(start + window, n), without overflowing the sum.
        let end = start + window.min(n - start);
        let first = encoding.offsets[start].0;
        let last = encoding.offsets[end - 1].1;
        if last <= first {
            return Err(DocumentSpansError::InvalidOffsets);
        }
        // Python's nonnegative character slice bounds clip at len(body).
        // Validate last>first before clipping, exactly as the source does.
        let first = first.min(original.char_len());
        let last = last.min(original.char_len());
        let text = original
            .slice(first..last)
            .expect("ordered clipped original-character bounds");
        let offset = original
            .slice(0..first)
            .expect("clipped original-character prefix")
            .len();
        passages.push(DocumentPassage {
            text: text.to_owned(),
            span: Utf8Span {
                offset,
                length: text.len(),
            },
        });
        // Do not stop when this window reaches n: range(0,n,stride) keeps
        // the final smaller window even when the preceding window overlaps it.
    }
    if !(1..=256).contains(&passages.len()) {
        return Err(DocumentSpansError::PassageBounds);
    }
    // Python first materializes the complete generator, checks its size, then
    // re-encodes in order. A later bad span precedes ANY passage encoding.
    for passage in &passages {
        let count = encode(&passage.text, true)
            .map_err(DocumentSpansError::Encoding)?
            .count;
        if count > 512 {
            return Err(DocumentSpansError::PassageTokenBound);
        }
    }
    Ok(passages)
}

#[cfg(test)]
mod tests;
