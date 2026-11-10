//! Original-text slicing in Python character coordinates. Tokenizer offsets
//! already refer to the original text; do not expand them over ignored bytes.
use std::ops::Range;

pub(crate) struct OriginalText<'a> {
    text: &'a str,
    boundaries: Vec<usize>,
}

impl<'a> OriginalText<'a> {
    pub(crate) fn new(text: &'a str) -> Self {
        Self {
            text,
            boundaries: text
                .char_indices()
                .map(|(i, _)| i)
                .chain([text.len()])
                .collect(),
        }
    }

    pub(crate) fn char_len(&self) -> usize {
        self.boundaries.len() - 1
    }

    pub(crate) fn slice(&self, chars: Range<usize>) -> Option<&'a str> {
        let start = *self.boundaries.get(chars.start)?;
        let end = *self.boundaries.get(chars.end)?;
        self.text.get(start..end)
    }
}
