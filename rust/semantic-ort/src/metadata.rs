use crate::Error;
use cairn_semantic_tokenizer::BatchOneTensors;

pub const DIMENSIONS: usize = 384;
pub const MAX_SEQUENCE: usize = 512;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ElementType {
    Float32,
    Int64,
    Other(i32),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NodeMetadata {
    pub name: String,
    pub element_type: ElementType,
    /// Negative dimensions are symbolic/unknown, as returned by ORT.
    pub dimensions: Vec<i64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Input {
    Ids,
    AttentionMask,
    TokenTypeIds,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SessionMetadata {
    pub inputs: Vec<NodeMetadata>,
    pub outputs: Vec<NodeMetadata>,
}

impl SessionMetadata {
    pub(crate) fn validate(&self) -> Result<Vec<Input>, Error> {
        let mut plan = Vec::new();
        for node in &self.inputs {
            let input = match node.name.as_str() {
                "input_ids" => Input::Ids,
                "attention_mask" => Input::AttentionMask,
                "token_type_ids" => Input::TokenTypeIds,
                _ => {
                    return Err(Error::Contract(format!(
                        "unexpected session input {}",
                        node.name
                    )))
                }
            };
            if plan.contains(&input)
                || node.element_type != ElementType::Int64
                || node.dimensions.len() != 2
            {
                return Err(Error::Contract(format!(
                    "invalid/duplicate int64 input metadata: {node:?}"
                )));
            }
            if !(node.dimensions[0] < 0 || node.dimensions[0] == 1) || node.dimensions[1] == 0 {
                return Err(Error::Contract(format!("input is not batch-one: {node:?}")));
            }
            plan.push(input);
        }
        if !plan.contains(&Input::Ids) {
            return Err(Error::Contract(
                "session does not advertise input_ids".into(),
            ));
        }
        let selected: Vec<_> = self
            .outputs
            .iter()
            .filter(|n| n.name == "last_hidden_state")
            .collect();
        if selected.len() != 1 {
            return Err(Error::Contract(
                "last_hidden_state must occur exactly once".into(),
            ));
        }
        let output = selected[0];
        if output.element_type != ElementType::Float32
            || output.dimensions.len() != 3
            || !(output.dimensions[0] < 0 || output.dimensions[0] == 1)
            || output.dimensions[1] == 0
            || !(output.dimensions[2] < 0 || output.dimensions[2] == DIMENSIONS as i64)
        {
            return Err(Error::Contract(format!(
                "invalid last_hidden_state metadata: {output:?}"
            )));
        }
        Ok(plan)
    }

    pub(crate) fn validate_batch(&self, batch: &BatchOneTensors) -> Result<usize, Error> {
        self.validate()?;
        let n = batch.input_ids.len();
        if n == 0
            || n > MAX_SEQUENCE
            || batch.shape != [1, n]
            || batch.attention_mask.len() != n
            || batch.token_type_ids.len() != n
            || batch.token_type_ids.iter().any(|&v| v != 0)
        {
            return Err(Error::Contract(
                "invalid owned batch-one int64 tensors or nonzero type IDs".into(),
            ));
        }
        for node in &self.inputs {
            if node.dimensions[1] >= 0 && node.dimensions[1] != n as i64 {
                return Err(Error::Contract(format!(
                    "sequence length conflicts with {} metadata",
                    node.name
                )));
            }
        }
        let out = self
            .outputs
            .iter()
            .find(|n| n.name == "last_hidden_state")
            .expect("validated output");
        if out.dimensions[1] >= 0 && out.dimensions[1] != n as i64 {
            return Err(Error::Contract(
                "output sequence length conflicts with input".into(),
            ));
        }
        Ok(n)
    }
}

/// Raw ORT float32 bits, BEFORE FastEmbed normalization and P3 rank arithmetic.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RawClsBits(pub [u32; DIMENSIONS]);

pub(crate) fn extract_cls(
    shape: &[i64],
    bits: &[u32],
    sequence: usize,
) -> Result<RawClsBits, Error> {
    if sequence == 0 || sequence > MAX_SEQUENCE || shape != [1, sequence as i64, DIMENSIONS as i64]
    {
        return Err(Error::Contract(format!(
            "unexpected last_hidden_state shape {shape:?}"
        )));
    }
    let count = sequence
        .checked_mul(DIMENSIONS)
        .ok_or_else(|| Error::Contract("output element count overflow".into()))?;
    if bits.len() != count {
        return Err(Error::Contract(
            "output element count differs from shape".into(),
        ));
    }
    let mut cls = [0; DIMENSIONS];
    cls.copy_from_slice(&bits[..DIMENSIONS]);
    Ok(RawClsBits(cls))
}
