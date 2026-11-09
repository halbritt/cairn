use crate::Error;
use tokenizers::Encoding;

/// Owned row-major int64 inputs for exactly one model Encoding. Each text is
/// encoded in its own one-element batch, so no neighbour changes its padding.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BatchOneTensors {
    pub shape: [usize; 2],
    pub input_ids: Vec<i64>,
    pub attention_mask: Vec<i64>,
    pub token_type_ids: Vec<i64>,
}

impl BatchOneTensors {
    pub fn from_encoding(encoding: &Encoding) -> Result<Self, Error> {
        let ids = encoding.get_ids();
        let mask = encoding.get_attention_mask();
        if ids.len() != mask.len() {
            return Err(Error::EncodingLengths {
                ids: ids.len(),
                mask: mask.len(),
            });
        }
        Ok(Self {
            shape: [1, ids.len()],
            input_ids: ids.iter().copied().map(i64::from).collect(),
            attention_mask: mask.iter().copied().map(i64::from).collect(),
            // FastEmbed OnnxTextModel.onnx_embed zero-fills this model feed;
            // the original Encoding's type IDs are neither read nor changed.
            token_type_ids: vec![0; ids.len()],
        })
    }
}
