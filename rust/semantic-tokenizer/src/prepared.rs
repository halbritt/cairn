use crate::{BatchOneTensors, Error};
use cairn_semantic_protocol::PyString;
use cairn_semantic_rank::backend::{DecodeTokens, SpecialTokens};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use tokenizers::{AddedToken, Encoding, PaddingParams, Tokenizer, TruncationParams};

/// Exact caller-owned local input bytes. This constructor neither discovers
/// assets nor opens paths, downloads files, loads ORT or constructs a model.
pub struct PreparedInputs<'a> {
    pub config: &'a [u8],
    pub tokenizer_config: &'a [u8],
    pub special_tokens_map: &'a [u8],
    pub tokenizer: &'a [u8],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ConsumedFile {
    pub name: &'static str,
    pub bytes: Vec<u8>,
    pub sha256: [u8; 32],
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PreparationNotice {
    IgnoredSpecialValue { name: String },
    IgnoredAddedTokenOption { name: String, option: String },
}

/// Owns the actually prepared model tokenizer and its serialized/deserialized
/// rank clone. Neither handle is exposed for external mutation.
pub struct PreparedTokenizer {
    model: Tokenizer,
    rank: Tokenizer,
    consumed: Vec<ConsumedFile>,
    special_ids: Vec<(String, Option<u32>)>,
    notices: Vec<PreparationNotice>,
}

fn invalid(file: &'static str, field: &str, detail: &'static str) -> Error {
    Error::Configuration {
        file,
        field: field.to_owned(),
        detail,
    }
}

fn json(file: &'static str, bytes: &[u8]) -> Result<Value, Error> {
    // preserve_order is necessary: Python dict insertion order determines new
    // added-token IDs. No arbitrary component recursion cap is introduced.
    let mut decoder = serde_json::Deserializer::from_slice(bytes);
    decoder.disable_recursion_limit();
    let mut values = decoder.into_iter::<Value>();
    let value = values
        .next()
        .transpose()
        .map_err(|source| Error::Json { file, source })?
        .ok_or_else(|| invalid(file, "", "empty JSON document"))?;
    if let Some(extra) = values.next() {
        extra.map_err(|source| Error::Json { file, source })?;
        return Err(invalid(file, "", "trailing JSON document"));
    }
    Ok(value)
}

fn fields<'a>(value: &'a Value, file: &'static str) -> Result<&'a Map<String, Value>, Error> {
    value
        .as_object()
        .ok_or_else(|| invalid(file, "", "expected object"))
}

fn length(value: &Value) -> Result<usize, Error> {
    let number = match value {
        Value::Bool(value) => Some(u64::from(*value)),
        Value::Number(value) => value.as_u64(),
        _ => None,
    };
    number
        .and_then(|value| usize::try_from(value).ok())
        .ok_or_else(|| {
            invalid(
                "tokenizer_config.json",
                "max_length",
                "expected nonnegative usize",
            )
        })
}

fn max_context(config: &Map<String, Value>) -> Result<usize, Error> {
    match (config.get("model_max_length"), config.get("max_length")) {
        (None, None) => Err(invalid(
            "tokenizer_config.json",
            "model_max_length/max_length",
            "both length keys missing",
        )),
        (Some(value), None) | (None, Some(value)) => length(value),
        (Some(model), Some(max)) => {
            // Select before usize extraction, as Python min does. In
            // particular a large floating model_max_length may coexist with
            // a smaller valid integer max_length; the unused float must not
            // fail extraction. Equal values preserve the first argument.
            let second_is_less = numeric_cmp(max, model)
                .ok_or_else(|| {
                    invalid(
                        "tokenizer_config.json",
                        "max_length",
                        "incomparable length values",
                    )
                })?
                .is_lt();
            length(if second_is_less { max } else { model })
        }
    }
}

fn numeric_cmp(left: &Value, right: &Value) -> Option<std::cmp::Ordering> {
    let integer = |value: &Value| match value {
        Value::Bool(value) => Some(i128::from(u8::from(*value))),
        Value::Number(value) => value
            .as_i64()
            .map(i128::from)
            .or_else(|| value.as_u64().map(i128::from)),
        _ => None,
    };
    let float = |value: &Value| value.as_f64();
    // Do not round an integer to f64 when Python min compares mixed values:
    // that can change which value (and therefore which input type) is chosen.
    let int_float = |integer: i128, float: f64| {
        use std::cmp::Ordering;
        if float >= i128::MAX as f64 {
            return Ordering::Less;
        }
        if float < i128::MIN as f64 {
            return Ordering::Greater;
        }
        match integer.cmp(&(float.trunc() as i128)) {
            Ordering::Equal if float.fract() > 0.0 => Ordering::Less,
            Ordering::Equal if float.fract() < 0.0 => Ordering::Greater,
            result => result,
        }
    };
    match (integer(left), integer(right)) {
        (Some(left), Some(right)) => Some(left.cmp(&right)),
        (Some(left), None) => Some(int_float(left, float(right)?)),
        (None, Some(right)) => Some(int_float(right, float(left)?).reverse()),
        (None, None) => float(left)?.partial_cmp(&float(right)?),
    }
}

fn token_content<'a>(name: &str, token: &'a Map<String, Value>) -> Result<&'a str, Error> {
    match token.get("content") {
        None => Ok(""),
        Some(Value::String(value)) => Ok(value),
        _ => Err(invalid(
            "special_tokens_map.json",
            name,
            "AddedToken content must be a string",
        )),
    }
}

fn added_token(
    name: &str,
    options: &Map<String, Value>,
    notices: &mut Vec<PreparationNotice>,
) -> Result<AddedToken, Error> {
    let content = token_content(name, options)?;
    let boolean = |key: &str| -> Result<Option<bool>, Error> {
        options
            .get(key)
            .map(|value| {
                value.as_bool().ok_or_else(|| {
                    invalid(
                        "special_tokens_map.json",
                        key,
                        "AddedToken flag must be boolean",
                    )
                })
            })
            .transpose()
    };
    // Python validates the constructor's special kwarg, then its
    // add_special_tokens wrapper sets PyAddedToken.special=true BEFORE
    // get_token materializes the Rust AddedToken. An unspecified normalized
    // flag therefore defaults to false; an explicit one still overrides it.
    let _constructor_special = boolean("special")?;
    let mut token = AddedToken::from(content, true);
    if let Some(value) = boolean("single_word")? {
        token = token.single_word(value);
    }
    if let Some(value) = boolean("lstrip")? {
        token = token.lstrip(value);
    }
    if let Some(value) = boolean("rstrip")? {
        token = token.rstrip(value);
    }
    if let Some(value) = boolean("normalized")? {
        token = token.normalized(value);
    }
    for key in options.keys() {
        if !matches!(
            key.as_str(),
            "content" | "single_word" | "lstrip" | "rstrip" | "normalized" | "special"
        ) {
            notices.push(PreparationNotice::IgnoredAddedTokenOption {
                name: name.to_owned(),
                option: key.clone(),
            });
        }
    }
    Ok(token)
}

fn utf8(text: &PyString) -> Result<String, Error> {
    let bytes = text.strict_utf8().map_err(Error::TextUtf8)?;
    Ok(String::from_utf8(bytes).expect("published strict_utf8 returns valid UTF8"))
}

impl PreparedTokenizer {
    /// Mirrors pinned FastEmbed 0.8.0 load_tokenizer. Configuration not consumed
    /// in a branch (e.g. pad fields when padding exists) is not spuriously read.
    pub fn from_bytes(input: PreparedInputs<'_>) -> Result<Self, Error> {
        let config = json("config.json", input.config)?;
        let tokenizer_config = json("tokenizer_config.json", input.tokenizer_config)?;
        let tokenizer_config = fields(&tokenizer_config, "tokenizer_config.json")?;
        let maximum = max_context(tokenizer_config)?;
        let tokens_map = json("special_tokens_map.json", input.special_tokens_map)?;
        let mut model = Tokenizer::from_bytes(input.tokenizer)
            .map_err(|source| Error::tokenizer("model tokenizer JSON", source))?;
        model
            .with_truncation(Some(TruncationParams {
                max_length: maximum,
                ..Default::default()
            }))
            .map_err(|source| Error::tokenizer("model truncation", source))?;
        if model.get_padding().is_none() {
            let config = fields(&config, "config.json")?;
            let pad_id = match config.get("pad_token_id") {
                None => 0,
                Some(Value::Bool(value)) => u32::from(*value),
                Some(value) => value
                    .as_u64()
                    .and_then(|value| u32::try_from(value).ok())
                    .ok_or_else(|| invalid("config.json", "pad_token_id", "expected u32"))?,
            };
            let pad_token = tokenizer_config
                .get("pad_token")
                .and_then(Value::as_str)
                .ok_or_else(|| {
                    invalid(
                        "tokenizer_config.json",
                        "pad_token",
                        "missing or nonstring pad token",
                    )
                })?;
            model.with_padding(Some(PaddingParams {
                pad_id,
                pad_token: pad_token.to_owned(),
                ..Default::default()
            }));
        }
        let tokens_map = fields(&tokens_map, "special_tokens_map.json")?;
        let mut notices = Vec::new();
        for (name, value) in tokens_map {
            match value {
                Value::String(text) => {
                    model
                        .add_special_tokens([AddedToken::from(text.as_str(), true)])
                        .map_err(|source| Error::tokenizer("add string special token", source))?;
                }
                Value::Object(options) => {
                    model
                        .add_special_tokens([added_token(name, options, &mut notices)?])
                        .map_err(|source| {
                            Error::tokenizer("add dictionary special token", source)
                        })?;
                }
                _ => notices.push(PreparationNotice::IgnoredSpecialValue { name: name.clone() }),
            }
        }
        let mut special_ids: Vec<(String, Option<u32>)> = Vec::new();
        for (name, value) in tokens_map {
            let text = match value {
                Value::String(text) => text.as_str(),
                Value::Object(options) => token_content(name, options)?,
                _ => continue,
            };
            let id = model.token_to_id(text);
            if let Some((_, previous)) = special_ids.iter_mut().find(|(key, _)| key == text) {
                *previous = id;
            } else {
                special_ids.push((text.to_owned(), id));
            }
        }
        // Source Scorer serializes the prepared model tokenizer, reparses it,
        // then disables BOTH policies on the separate rank tokenizer.
        let serialized = model
            .to_string(false)
            .map_err(|source| Error::tokenizer("serialize rank clone", source))?;
        let mut rank = Tokenizer::from_bytes(serialized.as_bytes())
            .map_err(|source| Error::tokenizer("deserialize rank clone", source))?;
        rank.with_truncation(None)
            .map_err(|source| Error::tokenizer("rank no truncation", source))?;
        rank.with_padding(None);
        let consumed = [
            ("config.json", input.config),
            ("tokenizer_config.json", input.tokenizer_config),
            ("special_tokens_map.json", input.special_tokens_map),
            ("tokenizer.json", input.tokenizer),
        ]
        .into_iter()
        .map(|(name, bytes)| ConsumedFile {
            name,
            bytes: bytes.to_vec(),
            sha256: Sha256::digest(bytes).into(),
        })
        .collect();
        Ok(Self {
            model,
            rank,
            consumed,
            special_ids,
            notices,
        })
    }

    pub fn consumed_files(&self) -> &[ConsumedFile] {
        &self.consumed
    }
    pub fn special_token_ids(&self) -> &[(String, Option<u32>)] {
        &self.special_ids
    }
    pub fn notices(&self) -> &[PreparationNotice] {
        &self.notices
    }

    /// Prepared model configuration for a later consumed-input derivation
    /// record. This serialization is not a worker/model identity attestation.
    pub fn model_json(&self) -> Result<String, Error> {
        self.model
            .to_string(false)
            .map_err(|source| Error::tokenizer("serialize prepared model", source))
    }

    pub fn rank_encode(&self, text: &PyString, policy: SpecialTokens) -> Result<Vec<u32>, Error> {
        let text = utf8(text)?;
        let encoding = self
            .rank
            .encode(text.as_str(), policy == SpecialTokens::Default)
            .map_err(|source| Error::tokenizer("rank encode", source))?;
        Ok(encoding.get_ids().to_vec())
    }

    pub fn rank_decode(&self, ids: &[u32], policy: DecodeTokens) -> Result<PyString, Error> {
        let skip = match policy {
            DecodeTokens::KeepSpecialTokens => false,
        };
        let text = self
            .rank
            .decode(ids, skip)
            .map_err(|source| Error::tokenizer("rank decode", source))?;
        Ok(
            PyString::from_codepoints(text.chars().map(u32::from).collect())
                .expect("Rust scalar string"),
        )
    }

    pub fn model_encoding(&self, text: &PyString) -> Result<Encoding, Error> {
        let text = utf8(text)?;
        let mut encodings = self
            .model
            .encode_batch(vec![text.as_str()], true)
            .map_err(|source| Error::tokenizer("model batch-one encode", source))?;
        if encodings.len() != 1 {
            return Err(Error::EncodingBatchCount(encodings.len()));
        }
        Ok(encodings.remove(0))
    }

    pub fn model_tensors_in_order(
        &self,
        texts: &[PyString],
    ) -> Result<Vec<BatchOneTensors>, Error> {
        texts
            .iter()
            .map(|text| BatchOneTensors::from_encoding(&self.model_encoding(text)?))
            .collect()
    }
}
