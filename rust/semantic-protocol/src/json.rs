//! Selected CPython 3.11.15 byte/JSON semantics, without a worker or backend.
//! Unicode positions count input bytes; JSON syntax positions count decoded
//! code points. Framing/conversion/rendering use NotApplicable. Depth pending is
//! an implementation boundary, never a Source exception or runtime attestation.

use std::collections::BTreeMap;

use crate::{PyInteger, PyObject, PyString, Value};

pub const MAX_CONTAINERS: usize = 64;
pub const INTEGER_DIGITS: usize = 4300;
pub const RANK_INPUT_LIMIT: usize = 8 * 1024 * 1024;
pub const EMBEDDING_INPUT_LIMIT: usize = 512 * 1024;
pub const RANK_REPLY_LIMIT: usize = 65536;
pub const EMBEDDING_REPLY_LIMIT: usize = 4 * 1024 * 1024;
pub const CACHED_RANK_REPLY_LIMIT: usize = 600 * 1024 + RANK_REPLY_LIMIT;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExceptionFamily {
    JsonDecodeError,
    UnicodeDecodeError,
    ValueError,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CodecSite {
    InputSize,
    MissingLineFeed,
    Unicode,
    JsonSyntax,
    IntegerDigits,
    FloatConversion,
    NonFinite,
    OrdinaryRankSize,
    CachedRankSize,
    EmbeddingSize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Position {
    InputByte(usize),
    DecodedCodePoint(usize),
    NotApplicable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CodecError {
    pub family: ExceptionFamily,
    pub site: CodecSite,
    pub position: Position,
}

fn value_error(site: CodecSite) -> CodecError {
    CodecError {
        family: ExceptionFamily::ValueError,
        site,
        position: Position::NotApplicable,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputMode {
    RankOnce,
    RankLine,
    EmbeddingLine,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DecodeProfile {
    SourceBytesDefault4300,
}

#[derive(Debug, PartialEq, Eq)]
pub struct DecodedInput {
    value: Value,
    consumed: usize,
}

impl DecodedInput {
    pub fn value(&self) -> &Value {
        &self.value
    }
    pub fn into_value(self) -> Value {
        self.value
    }
    pub fn consumed(&self) -> usize {
        self.consumed
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DecodeNeedsSourceDepth<'a> {
    input: &'a [u8],
    mode: InputMode,
    consumed: usize,
}

impl<'a> DecodeNeedsSourceDepth<'a> {
    pub fn input(&self) -> &'a [u8] {
        self.input
    }
    pub fn mode(&self) -> InputMode {
        self.mode
    }
    pub fn consumed(&self) -> usize {
        self.consumed
    }
    pub fn profile(&self) -> DecodeProfile {
        DecodeProfile::SourceBytesDefault4300
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum DecodeOutcome<'a> {
    Decoded(DecodedInput),
    EndOfInput,
    NeedsSourceDepth(DecodeNeedsSourceDepth<'a>),
}

fn selected_read(mode: InputMode, input: &[u8]) -> Result<Option<&[u8]>, CodecError> {
    if mode == InputMode::RankOnce {
        let selected = &input[..input.len().min(RANK_INPUT_LIMIT + 1)];
        if selected.len() > RANK_INPUT_LIMIT {
            return Err(value_error(CodecSite::InputSize));
        }
        return Ok(Some(selected));
    }
    if input.is_empty() {
        return Ok(None);
    }
    let (cap, allowed) = match mode {
        InputMode::RankLine => (RANK_INPUT_LIMIT + 2, RANK_INPUT_LIMIT + 1),
        InputMode::EmbeddingLine => (EMBEDDING_INPUT_LIMIT + 1, EMBEDDING_INPUT_LIMIT),
        InputMode::RankOnce => unreachable!(),
    };
    let read = &input[..input.len().min(cap)];
    let end = read
        .iter()
        .position(|&byte| byte == b'\n')
        .map_or(read.len(), |i| i + 1);
    let selected = &read[..end];
    if selected.len() > allowed {
        return Err(value_error(CodecSite::InputSize));
    }
    if selected.last() != Some(&b'\n') {
        return Err(value_error(CodecSite::MissingLineFeed));
    }
    Ok(Some(selected))
}

#[derive(Clone, Copy)]
enum Encoding {
    Utf8,
    Utf16Le,
    Utf16Be,
    Utf32Le,
    Utf32Be,
}

fn detect(input: &[u8]) -> (Encoding, usize) {
    if input.starts_with(&[0, 0, 0xfe, 0xff]) {
        return (Encoding::Utf32Be, 4);
    }
    if input.starts_with(&[0xff, 0xfe, 0, 0]) {
        return (Encoding::Utf32Le, 4);
    }
    if input.starts_with(&[0xfe, 0xff]) {
        return (Encoding::Utf16Be, 2);
    }
    if input.starts_with(&[0xff, 0xfe]) {
        return (Encoding::Utf16Le, 2);
    }
    if input.starts_with(&[0xef, 0xbb, 0xbf]) {
        return (Encoding::Utf8, 3);
    }
    if input.len() >= 4 {
        if input[0] == 0 {
            return (
                if input[1] == 0 {
                    Encoding::Utf32Be
                } else {
                    Encoding::Utf16Be
                },
                0,
            );
        }
        if input[1] == 0 {
            return (
                if input[2] != 0 || input[3] != 0 {
                    Encoding::Utf16Le
                } else {
                    Encoding::Utf32Le
                },
                0,
            );
        }
    } else if input.len() == 2 {
        if input[0] == 0 {
            return (Encoding::Utf16Be, 0);
        }
        if input[1] == 0 {
            return (Encoding::Utf16Le, 0);
        }
    }
    (Encoding::Utf8, 0)
}

fn unicode_error(position: usize) -> CodecError {
    CodecError {
        family: ExceptionFamily::UnicodeDecodeError,
        site: CodecSite::Unicode,
        position: Position::InputByte(position),
    }
}

fn unicode(input: &[u8]) -> Result<Vec<u32>, CodecError> {
    let (encoding, skip) = detect(input);
    let mut position = skip;
    let mut out = Vec::new();
    while position < input.len() {
        let start = position;
        let cp = match encoding {
            Encoding::Utf8 => {
                let first = input[position];
                let (length, minimum, mut cp) = match first {
                    0..=0x7f => (1, 0, u32::from(first)),
                    0xc2..=0xdf => (2, 0x80, u32::from(first & 0x1f)),
                    0xe0..=0xef => (3, 0x800, u32::from(first & 0x0f)),
                    0xf0..=0xf4 => (4, 0x10000, u32::from(first & 0x07)),
                    _ => return Err(unicode_error(start)),
                };
                if input.len() - position < length {
                    return Err(unicode_error(start));
                }
                for &byte in &input[position + 1..position + length] {
                    if !(0x80..=0xbf).contains(&byte) {
                        return Err(unicode_error(start));
                    }
                    cp = (cp << 6) | u32::from(byte & 0x3f);
                }
                if cp < minimum || cp > 0x10ffff {
                    return Err(unicode_error(start));
                }
                position += length;
                cp // Surrogate triples intentionally survive.
            }
            Encoding::Utf16Le | Encoding::Utf16Be => {
                if input.len() - position < 2 {
                    return Err(unicode_error(start));
                }
                let unit = |i| {
                    let pair = [input[i], input[i + 1]];
                    if matches!(encoding, Encoding::Utf16Le) {
                        u16::from_le_bytes(pair)
                    } else {
                        u16::from_be_bytes(pair)
                    }
                };
                let high = unit(position);
                position += 2;
                if (0xd800..=0xdbff).contains(&high)
                    && input.len() - position >= 2
                    && (0xdc00..=0xdfff).contains(&unit(position))
                {
                    let low = unit(position);
                    position += 2;
                    0x10000 + ((u32::from(high) - 0xd800) << 10) + u32::from(low) - 0xdc00
                } else {
                    u32::from(high)
                }
            }
            Encoding::Utf32Le | Encoding::Utf32Be => {
                if input.len() - position < 4 {
                    return Err(unicode_error(start));
                }
                let bytes = [
                    input[position],
                    input[position + 1],
                    input[position + 2],
                    input[position + 3],
                ];
                let cp = if matches!(encoding, Encoding::Utf32Le) {
                    u32::from_le_bytes(bytes)
                } else {
                    u32::from_be_bytes(bytes)
                };
                if cp > 0x10ffff {
                    return Err(unicode_error(start));
                }
                position += 4;
                cp // UTF32 surrogate units never join here.
            }
        };
        out.push(cp);
    }
    Ok(out)
}

#[derive(Debug)]
enum ParseFailure {
    Source(CodecError),
    Depth,
}

struct Parser<'a> {
    input: &'a [u32],
    position: usize,
}

impl Parser<'_> {
    fn syntax(&self) -> ParseFailure {
        ParseFailure::Source(CodecError {
            family: ExceptionFamily::JsonDecodeError,
            site: CodecSite::JsonSyntax,
            position: Position::DecodedCodePoint(self.position),
        })
    }
    fn peek(&self) -> Option<u32> {
        self.input.get(self.position).copied()
    }
    fn whitespace(&mut self) {
        while matches!(self.peek(), Some(0x20 | 0x09 | 0x0a | 0x0d)) {
            self.position += 1;
        }
    }
    fn starts(&self, text: &str) -> bool {
        self.input[self.position..]
            .iter()
            .copied()
            .take(text.len())
            .eq(text.bytes().map(u32::from))
    }
    fn value(&mut self, depth: usize) -> Result<Value, ParseFailure> {
        self.whitespace();
        match self.peek() {
            Some(0x22) => Ok(Value::String(self.string()?)),
            Some(0x5b) => self.array(depth),
            Some(0x7b) => self.object(depth),
            Some(0x6e) if self.starts("null") => {
                self.position += 4;
                Ok(Value::Null)
            }
            Some(0x74) if self.starts("true") => {
                self.position += 4;
                Ok(Value::Bool(true))
            }
            Some(0x66) if self.starts("false") => {
                self.position += 5;
                Ok(Value::Bool(false))
            }
            Some(0x4e) if self.starts("NaN") => {
                self.position += 3;
                Ok(Value::FloatBits(0x7ff8_0000_0000_0000))
            }
            Some(0x49) if self.starts("Infinity") => {
                self.position += 8;
                Ok(Value::FloatBits(0x7ff0_0000_0000_0000))
            }
            Some(0x2d) if self.starts("-Infinity") => {
                self.position += 9;
                Ok(Value::FloatBits(0xfff0_0000_0000_0000))
            }
            Some(0x2d | 0x30..=0x39) => self.number(),
            _ => Err(self.syntax()),
        }
    }
    fn string(&mut self) -> Result<PyString, ParseFailure> {
        self.position += 1; // Caller observed the opening quote.
        let mut out = Vec::new();
        loop {
            let cp = self.peek().ok_or_else(|| self.syntax())?;
            self.position += 1;
            match cp {
                0x22 => return Ok(PyString(out)),
                0..=0x1f => return Err(self.syntax()),
                0x5c => {
                    let escape = self.peek().ok_or_else(|| self.syntax())?;
                    self.position += 1;
                    let cp = match escape {
                        0x22 | 0x5c | 0x2f => escape,
                        0x62 => 8,
                        0x66 => 12,
                        0x6e => 10,
                        0x72 => 13,
                        0x74 => 9,
                        0x75 => {
                            let high = self.hex4().ok_or_else(|| self.syntax())?;
                            if (0xd800..=0xdbff).contains(&high) && self.starts("\\u") {
                                let saved = self.position;
                                self.position += 2;
                                if let Some(low) = self.hex4() {
                                    if (0xdc00..=0xdfff).contains(&low) {
                                        out.push(0x10000 + ((high - 0xd800) << 10) + low - 0xdc00);
                                        continue;
                                    }
                                }
                                self.position = saved;
                            }
                            high
                        }
                        _ => return Err(self.syntax()),
                    };
                    out.push(cp);
                }
                _ => out.push(cp),
            }
        }
    }
    fn hex4(&mut self) -> Option<u32> {
        let digits = self.input.get(self.position..self.position + 4)?;
        let mut value = 0;
        for &cp in digits {
            let digit = match cp {
                0x30..=0x39 => cp - 0x30,
                0x61..=0x66 => cp - 0x61 + 10,
                0x41..=0x46 => cp - 0x41 + 10,
                _ => return None,
            };
            value = value * 16 + digit;
        }
        self.position += 4;
        Some(value)
    }
    fn array(&mut self, depth: usize) -> Result<Value, ParseFailure> {
        if depth >= MAX_CONTAINERS {
            return Err(ParseFailure::Depth);
        }
        self.position += 1;
        self.whitespace();
        let mut values = Vec::new();
        if self.peek() == Some(0x5d) {
            self.position += 1;
            return Ok(Value::Array(values));
        }
        loop {
            values.push(self.value(depth + 1)?);
            self.whitespace();
            match self.peek() {
                Some(0x5d) => {
                    self.position += 1;
                    return Ok(Value::Array(values));
                }
                Some(0x2c) => {
                    self.position += 1;
                }
                _ => return Err(self.syntax()),
            }
        }
    }
    fn object(&mut self, depth: usize) -> Result<Value, ParseFailure> {
        if depth >= MAX_CONTAINERS {
            return Err(ParseFailure::Depth);
        }
        self.position += 1;
        self.whitespace();
        let mut entries: Vec<(PyString, Value)> = Vec::new();
        let mut seen: BTreeMap<Vec<u32>, usize> = BTreeMap::new();
        if self.peek() == Some(0x7d) {
            self.position += 1;
            return Ok(Value::Object(PyObject(entries)));
        }
        loop {
            self.whitespace();
            if self.peek() != Some(0x22) {
                return Err(self.syntax());
            }
            let key = self.string()?;
            self.whitespace();
            if self.peek() != Some(0x3a) {
                return Err(self.syntax());
            }
            self.position += 1;
            let value = self.value(depth + 1)?; // Even overwritten values convert.
            if let Some(&index) = seen.get(key.codepoints()) {
                entries[index].1 = value;
            } else {
                seen.insert(key.codepoints().to_vec(), entries.len());
                entries.push((key, value));
            }
            self.whitespace();
            match self.peek() {
                Some(0x7d) => {
                    self.position += 1;
                    return Ok(Value::Object(PyObject(entries)));
                }
                Some(0x2c) => {
                    self.position += 1;
                }
                _ => return Err(self.syntax()),
            }
        }
    }
    fn number(&mut self) -> Result<Value, ParseFailure> {
        let start = self.position;
        let negative = self.peek() == Some(0x2d);
        if negative {
            self.position += 1;
        }
        let digits = self.position;
        match self.peek() {
            Some(0x30) => self.position += 1,
            Some(0x31..=0x39) => {
                self.position += 1;
                while matches!(self.peek(), Some(0x30..=0x39)) {
                    self.position += 1;
                }
            }
            _ => return Err(self.syntax()),
        }
        let integer_end = self.position;
        let mut float = false;
        if self.peek() == Some(0x2e)
            && matches!(self.input.get(self.position + 1), Some(0x30..=0x39))
        {
            float = true;
            self.position += 2;
            while matches!(self.peek(), Some(0x30..=0x39)) {
                self.position += 1;
            }
        }
        if matches!(self.peek(), Some(0x65 | 0x45)) {
            let mut end = self.position + 1;
            if matches!(self.input.get(end), Some(0x2b | 0x2d)) {
                end += 1;
            }
            if matches!(self.input.get(end), Some(0x30..=0x39)) {
                float = true;
                self.position = end + 1;
                while matches!(self.peek(), Some(0x30..=0x39)) {
                    self.position += 1;
                }
            }
        }
        if float {
            let token: String = self.input[start..self.position]
                .iter()
                .map(|&cp| char::from(cp as u8))
                .collect();
            let value: f64 = token
                .parse()
                .map_err(|_| ParseFailure::Source(value_error(CodecSite::FloatConversion)))?;
            return Ok(Value::FloatBits(value.to_bits()));
        }
        if integer_end - digits > INTEGER_DIGITS {
            return Err(ParseFailure::Source(value_error(CodecSite::IntegerDigits)));
        }
        let mut magnitude: Vec<u32> = Vec::new();
        for &cp in &self.input[digits..integer_end] {
            let mut carry = u64::from(cp - 0x30);
            for limb in &mut magnitude {
                let next = u64::from(*limb) * 10 + carry;
                *limb = next as u32;
                carry = next >> 32;
            }
            if carry != 0 {
                magnitude.push(carry as u32);
            }
        }
        Ok(Value::Integer(PyInteger::new(negative, magnitude)))
    }
}

/// Completed snapshot/EOF semantics only, not incremental socket/worker state.
/// Whole selected-byte Unicode conversion precedes all JSON syntax decisions.
pub fn decode_input(mode: InputMode, complete: &[u8]) -> Result<DecodeOutcome<'_>, CodecError> {
    let Some(selected) = selected_read(mode, complete)? else {
        return Ok(DecodeOutcome::EndOfInput);
    };
    let codepoints = unicode(selected)?;
    let mut parser = Parser {
        input: &codepoints,
        position: 0,
    };
    match parser.value(0) {
        Ok(value) => {
            parser.whitespace();
            if parser.position != codepoints.len() {
                let ParseFailure::Source(error) = parser.syntax() else {
                    unreachable!()
                };
                return Err(error);
            }
            Ok(DecodeOutcome::Decoded(DecodedInput {
                value,
                consumed: selected.len(),
            }))
        }
        Err(ParseFailure::Source(error)) => Err(error),
        Err(ParseFailure::Depth) => Ok(DecodeOutcome::NeedsSourceDepth(DecodeNeedsSourceDepth {
            input: complete,
            mode,
            consumed: selected.len(),
        })),
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RenderProfile {
    RankOnce,
    RankStream,
    CachedRankStream,
    EmbeddingReply,
    CacheValue,
}

impl RenderProfile {
    fn compact(self) -> bool {
        self == Self::EmbeddingReply
    }
    fn allow_nonfinite(self) -> bool {
        self == Self::CacheValue
    }
    fn limit(self) -> Option<(usize, CodecSite)> {
        match self {
            Self::RankStream => Some((RANK_REPLY_LIMIT, CodecSite::OrdinaryRankSize)),
            Self::CachedRankStream => Some((CACHED_RANK_REPLY_LIMIT, CodecSite::CachedRankSize)),
            Self::EmbeddingReply => Some((EMBEDDING_REPLY_LIMIT, CodecSite::EmbeddingSize)),
            Self::RankOnce | Self::CacheValue => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RenderInput<'a> {
    Value(&'a Value),
    RankStream {
        id: &'a PyString,
        result: &'a Value,
        cache: Option<&'a Value>,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct NeedsSourceDepth<'a> {
    input: RenderInput<'a>,
    profile: RenderProfile,
}

impl<'a> NeedsSourceDepth<'a> {
    pub fn input(&self) -> RenderInput<'a> {
        self.input
    }
    pub fn profile(&self) -> RenderProfile {
        self.profile
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum EncodeOutcome<'a> {
    Complete(Vec<u8>),
    NeedsSourceDepth(NeedsSourceDepth<'a>),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MeasureOutcome<'a> {
    Complete(u128),
    NeedsSourceDepth(NeedsSourceDepth<'a>),
}

trait Sink {
    fn bytes(&mut self, bytes: &[u8]);
    fn byte(&mut self, byte: u8) {
        self.bytes(&[byte]);
    }
}

#[derive(Default)]
struct ByteSink(Vec<u8>);
impl Sink for ByteSink {
    fn bytes(&mut self, bytes: &[u8]) {
        self.0.extend_from_slice(bytes);
    }
}

#[derive(Default)]
struct CountSink(u128);
impl Sink for CountSink {
    fn bytes(&mut self, bytes: &[u8]) {
        self.0 += bytes.len() as u128;
    }
}

#[derive(Debug)]
enum WalkFailure {
    Source(CodecError),
    Depth,
}
impl From<CodecError> for WalkFailure {
    fn from(error: CodecError) -> Self {
        Self::Source(error)
    }
}

fn integer_decimal(integer: &PyInteger) -> Result<String, CodecError> {
    // A normalized 448-limb magnitude is at least 2^(32*447) = 2^14304,
    // strictly greater than 10^4300. Reject BEFORE cloning or decimal division.
    // 447 limbs alone prove no digit disposition; perform exact conversion.
    if integer.magnitude().len() >= 448 {
        return Err(value_error(CodecSite::IntegerDigits));
    }
    let mut limbs = integer.magnitude().to_vec();
    let mut chunks = Vec::new();
    while !limbs.is_empty() {
        let mut remainder = 0u64;
        for limb in limbs.iter_mut().rev() {
            let next = (remainder << 32) | u64::from(*limb);
            *limb = (next / 1_000_000_000) as u32;
            remainder = next % 1_000_000_000;
        }
        while limbs.last() == Some(&0) {
            limbs.pop();
        }
        chunks.push(remainder as u32);
    }
    let mut decimal = if let Some(last) = chunks.pop() {
        last.to_string()
    } else {
        "0".to_owned()
    };
    for chunk in chunks.iter().rev() {
        use std::fmt::Write;
        write!(&mut decimal, "{chunk:09}").expect("String formatting cannot fail");
    }
    if decimal.len() > INTEGER_DIGITS {
        return Err(value_error(CodecSite::IntegerDigits));
    }
    if integer.is_negative() {
        decimal.insert(0, '-');
    }
    Ok(decimal)
}

fn float_decimal(bits: u64, profile: RenderProfile) -> Result<String, CodecError> {
    let value = f64::from_bits(bits);
    if !value.is_finite() {
        if !profile.allow_nonfinite() {
            return Err(value_error(CodecSite::NonFinite));
        }
        return Ok(if value.is_nan() {
            "NaN"
        } else if value.is_sign_negative() {
            "-Infinity"
        } else {
            "Infinity"
        }
        .to_owned());
    }
    if value == 0.0 {
        return Ok(if bits >> 63 == 0 { "0.0" } else { "-0.0" }.to_owned());
    }
    let mut buffer = ryu::Buffer::new();
    let shortest = buffer.format_finite(value.abs());
    let (mantissa, exponent) = match shortest.split_once('e') {
        Some((m, e)) => (
            m,
            e.parse::<i32>()
                .expect("Ryu finite exponent is decimal i32"),
        ),
        None => (shortest, 0),
    };
    let fraction = mantissa
        .split_once('.')
        .map_or(0, |(_, right)| right.len() as i32);
    let mut digits: String = mantissa.chars().filter(|&c| c != '.').collect();
    let mut scale = exponent - fraction;
    let leading = digits.bytes().take_while(|&c| c == b'0').count();
    digits.drain(..leading);
    while digits.len() > 1 && digits.ends_with('0') {
        digits.pop();
        scale += 1;
    }
    let decimal_exponent = digits.len() as i32 - 1 + scale;
    let mut out = if value.is_sign_negative() {
        "-".to_owned()
    } else {
        String::new()
    };
    if !(-4..16).contains(&decimal_exponent) {
        out.push(digits.as_bytes()[0] as char);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        out.push('e');
        out.push(if decimal_exponent < 0 { '-' } else { '+' });
        use std::fmt::Write;
        write!(&mut out, "{:02}", decimal_exponent.unsigned_abs())
            .expect("String formatting cannot fail");
    } else {
        let point = decimal_exponent + 1;
        if point <= 0 {
            out.push_str("0.");
            out.push_str(&"0".repeat((-point) as usize));
            out.push_str(&digits);
        } else if point as usize >= digits.len() {
            out.push_str(&digits);
            out.push_str(&"0".repeat(point as usize - digits.len()));
            out.push_str(".0");
        } else {
            out.push_str(&digits[..point as usize]);
            out.push('.');
            out.push_str(&digits[point as usize..]);
        }
    }
    Ok(out)
}

fn hex_escape(sink: &mut impl Sink, cp: u32) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    sink.bytes(&[
        b'\\',
        b'u',
        HEX[((cp >> 12) & 15) as usize],
        HEX[((cp >> 8) & 15) as usize],
        HEX[((cp >> 4) & 15) as usize],
        HEX[(cp & 15) as usize],
    ]);
}

fn emit_string(text: &PyString, sink: &mut impl Sink) {
    sink.byte(b'"');
    for &cp in text.codepoints() {
        match cp {
            0x22 => sink.bytes(b"\\\""),
            0x5c => sink.bytes(b"\\\\"),
            8 => sink.bytes(b"\\b"),
            12 => sink.bytes(b"\\f"),
            10 => sink.bytes(b"\\n"),
            13 => sink.bytes(b"\\r"),
            9 => sink.bytes(b"\\t"),
            0x20..=0x7e => sink.byte(cp as u8),
            0..=0xffff => hex_escape(sink, cp),
            _ => {
                let extra = cp - 0x10000;
                hex_escape(sink, 0xd800 + (extra >> 10));
                hex_escape(sink, 0xdc00 + (extra & 0x3ff));
            }
        }
    }
    sink.byte(b'"');
}

fn emit_value(
    value: &Value,
    depth: usize,
    profile: RenderProfile,
    sink: &mut impl Sink,
) -> Result<(), WalkFailure> {
    let comma: &[u8] = if profile.compact() { b"," } else { b", " };
    let colon: &[u8] = if profile.compact() { b":" } else { b": " };
    match value {
        Value::Null => sink.bytes(b"null"),
        Value::Bool(true) => sink.bytes(b"true"),
        Value::Bool(false) => sink.bytes(b"false"),
        Value::Integer(integer) => sink.bytes(integer_decimal(integer)?.as_bytes()),
        Value::FloatBits(bits) => sink.bytes(float_decimal(*bits, profile)?.as_bytes()),
        Value::String(text) => emit_string(text, sink),
        Value::Array(values) => {
            if depth >= MAX_CONTAINERS {
                return Err(WalkFailure::Depth);
            }
            sink.byte(b'[');
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    sink.bytes(comma);
                }
                emit_value(value, depth + 1, profile, sink)?;
            }
            sink.byte(b']');
        }
        Value::Object(object) => {
            if depth >= MAX_CONTAINERS {
                return Err(WalkFailure::Depth);
            }
            sink.byte(b'{');
            for (index, (key, value)) in object.entries().iter().enumerate() {
                if index != 0 {
                    sink.bytes(comma);
                }
                emit_string(key, sink);
                sink.bytes(colon);
                emit_value(value, depth + 1, profile, sink)?;
            }
            sink.byte(b'}');
        }
    }
    Ok(())
}

fn emit(
    input: RenderInput<'_>,
    profile: RenderProfile,
    sink: &mut impl Sink,
) -> Result<(), WalkFailure> {
    match input {
        RenderInput::Value(value) => emit_value(value, 0, profile, sink),
        RenderInput::RankStream { id, result, cache } => {
            sink.bytes(b"{\"id\": ");
            emit_string(id, sink);
            sink.bytes(b", \"result\": ");
            emit_value(result, 1, profile, sink)?;
            if let Some(cache) = cache {
                sink.bytes(b", \"cache\": ");
                emit_value(cache, 1, profile, sink)?;
            }
            sink.byte(b'}');
            Ok(())
        }
    }
}

fn encode(input: RenderInput<'_>, profile: RenderProfile) -> Result<EncodeOutcome<'_>, CodecError> {
    let mut sink = ByteSink::default();
    match emit(input, profile, &mut sink) {
        Err(WalkFailure::Source(error)) => return Err(error),
        Err(WalkFailure::Depth) => {
            return Ok(EncodeOutcome::NeedsSourceDepth(NeedsSourceDepth {
                input,
                profile,
            }))
        }
        Ok(()) => (),
    }
    if profile != RenderProfile::CacheValue {
        sink.byte(b'\n');
    }
    // Conversion and pending work always precede any output-size disposition.
    if let Some((limit, site)) = profile.limit() {
        if sink.0.len() > limit {
            return Err(value_error(site));
        }
    }
    Ok(EncodeOutcome::Complete(sink.0))
}

/// No Source oneshot output bound. Go's stdout cap is a separate external gate.
pub fn encode_rank_once(value: &Value) -> Result<EncodeOutcome<'_>, CodecError> {
    encode(RenderInput::Value(value), RenderProfile::RankOnce)
}
pub fn encode_embedding_reply(value: &Value) -> Result<EncodeOutcome<'_>, CodecError> {
    encode(RenderInput::Value(value), RenderProfile::EmbeddingReply)
}

/// Cache profile only: default spaces, ensure_ascii, nonfinite allowed, no LF.
/// This is the same walk as every byte emitter; no size bound shortcuts it.
pub fn measure_cache_value(value: &Value) -> Result<MeasureOutcome<'_>, CodecError> {
    let input = RenderInput::Value(value);
    let profile = RenderProfile::CacheValue;
    let mut sink = CountSink::default();
    match emit(input, profile, &mut sink) {
        Ok(()) => Ok(MeasureOutcome::Complete(sink.0)),
        Err(WalkFailure::Source(error)) => Err(error),
        Err(WalkFailure::Depth) => Ok(MeasureOutcome::NeedsSourceDepth(NeedsSourceDepth {
            input,
            profile,
        })),
    }
}

/// Serialization-stage proof only. Private fields retain the SAME immutable
/// id/result whose ordinary encoding actually succeeded, before cache work.
#[derive(Debug, PartialEq, Eq)]
pub struct OrdinaryRankReply<'a> {
    id: &'a PyString,
    result: &'a Value,
    bytes: Vec<u8>,
}

impl<'a> OrdinaryRankReply<'a> {
    pub fn id(&self) -> &'a PyString {
        self.id
    }
    pub fn result(&self) -> &'a Value {
        self.result
    }
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }
    /// Caller still must obtain ordinary success BEFORE actual snapshot. Cache
    /// provenance, initialized scorer/model and I/O are not established here.
    pub fn encode_with_cache<'b>(
        &'b self,
        cache: &'b Value,
    ) -> Result<EncodeOutcome<'b>, CodecError> {
        encode(
            RenderInput::RankStream {
                id: self.id,
                result: self.result,
                cache: Some(cache),
            },
            RenderProfile::CachedRankStream,
        )
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum RankStreamPreparation<'a> {
    Ordinary(OrdinaryRankReply<'a>),
    NeedsSourceDepth(NeedsSourceDepth<'a>),
}

pub fn prepare_rank_stream_reply<'a>(
    id: &'a PyString,
    result: &'a Value,
) -> Result<RankStreamPreparation<'a>, CodecError> {
    match encode(
        RenderInput::RankStream {
            id,
            result,
            cache: None,
        },
        RenderProfile::RankStream,
    )? {
        EncodeOutcome::Complete(bytes) => Ok(RankStreamPreparation::Ordinary(OrdinaryRankReply {
            id,
            result,
            bytes,
        })),
        EncodeOutcome::NeedsSourceDepth(pending) => {
            Ok(RankStreamPreparation::NeedsSourceDepth(pending))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{cache, hash, CachePolicy, EmbedPrefix, FailureKind, IrError, Site};

    fn decoded(mode: InputMode, input: &[u8]) -> DecodedInput {
        match decode_input(mode, input).unwrap() {
            DecodeOutcome::Decoded(value) => value,
            other => panic!("SDP3 expected decoded input, got {other:?}"),
        }
    }
    fn value(input: &[u8]) -> Value {
        decoded(InputMode::RankOnce, input).into_value()
    }
    fn encoded(outcome: EncodeOutcome<'_>) -> Vec<u8> {
        match outcome {
            EncodeOutcome::Complete(bytes) => bytes,
            _ => panic!("SDP3 expected complete bytes"),
        }
    }
    fn once(value: &Value) -> Vec<u8> {
        encoded(encode_rank_once(value).unwrap())
    }
    fn count(value: &Value) -> u128 {
        match measure_cache_value(value).unwrap() {
            MeasureOutcome::Complete(size) => size,
            _ => panic!("SDP3 expected complete count"),
        }
    }
    fn text(text: &str) -> Value {
        Value::String(text.into())
    }
    fn object(entries: Vec<(&str, Value)>) -> Value {
        Value::Object(
            PyObject::new(entries.into_iter().map(|(k, v)| (k.into(), v)).collect()).unwrap(),
        )
    }
    fn integer(negative: bool, magnitude: Vec<u32>) -> Value {
        Value::Integer(PyInteger::new(negative, magnitude))
    }
    fn hex(input: &str) -> Vec<u8> {
        assert_eq!(input.len() % 2, 0);
        input
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let high = (pair[0] as char).to_digit(16).unwrap();
                let low = (pair[1] as char).to_digit(16).unwrap();
                (high * 16 + low) as u8
            })
            .collect()
    }
    fn string_codepoints(input: &[u8]) -> Vec<u32> {
        let Value::String(text) = value(input) else {
            panic!("SDP3 string fixture")
        };
        text.codepoints().to_vec()
    }
    fn family(mode: InputMode, input: &[u8], expected: ExceptionFamily) {
        assert_eq!(decode_input(mode, input).unwrap_err().family, expected);
    }
    // Independent bounded multiply construction, not the codec's division or
    // decimal output. A2 permits limb fixtures; expected decimal bytes below
    // remain literal one/zero/nine patterns, not codec-generated oracles.
    fn power10(exponent: usize) -> Vec<u32> {
        let mut limbs = vec![1u32];
        for _ in 0..exponent {
            let mut carry = 0u64;
            for limb in &mut limbs {
                let next = u64::from(*limb) * 10 + carry;
                *limb = next as u32;
                carry = next >> 32;
            }
            if carry != 0 {
                limbs.push(carry as u32);
            }
        }
        limbs
    }
    fn all_nines(digits: usize) -> Vec<u32> {
        let mut limbs = power10(digits);
        for limb in &mut limbs {
            let (next, borrow) = limb.overflowing_sub(1);
            *limb = next;
            if !borrow {
                break;
            }
        }
        limbs
    }
    fn nested(containers: usize) -> Value {
        let mut value = Value::Null;
        for _ in 0..containers {
            value = Value::Array(vec![value]);
        }
        value
    }
    fn ordinary<'a>(id: &'a PyString, result: &'a Value) -> OrdinaryRankReply<'a> {
        match prepare_rank_stream_reply(id, result).unwrap() {
            RankStreamPreparation::Ordinary(reply) => reply,
            _ => panic!("SDP3 expected ordinary serialization stage"),
        }
    }

    #[test]
    fn sdp3_rank_once_read_cap_and_empty_input_precede_unicode_and_json() {
        let mut input = vec![b' '; RANK_INPUT_LIMIT];
        input[0] = b'0';
        let read = decoded(InputMode::RankOnce, &input);
        assert_eq!(read.consumed(), 8 * 1024 * 1024);
        assert_eq!(read.value(), &integer(false, vec![]));
        input.push(0xff);
        assert_eq!(
            decode_input(InputMode::RankOnce, &input).unwrap_err().site,
            CodecSite::InputSize
        );
        family(InputMode::RankOnce, b"", ExceptionFamily::JsonDecodeError);
        assert_eq!(
            decode_input(InputMode::RankLine, b"").unwrap(),
            DecodeOutcome::EndOfInput
        );
        assert_eq!(
            decode_input(InputMode::EmbeddingLine, b"").unwrap(),
            DecodeOutcome::EndOfInput
        );
    }

    #[test]
    fn sdp3_byte_readline_caps_lf_eof_and_consumed_tail() {
        let mut rank = vec![b' '; RANK_INPUT_LIMIT];
        rank[0] = b'0';
        rank.push(b'\n');
        assert_eq!(
            decoded(InputMode::RankLine, &rank).consumed(),
            RANK_INPUT_LIMIT + 1
        );
        rank.insert(1, b' ');
        assert_eq!(
            decode_input(InputMode::RankLine, &rank).unwrap_err().site,
            CodecSite::InputSize
        );
        let mut embed = vec![b' '; EMBEDDING_INPUT_LIMIT];
        embed[0] = b'0';
        *embed.last_mut().unwrap() = b'\n';
        assert_eq!(
            decoded(InputMode::EmbeddingLine, &embed).consumed(),
            EMBEDDING_INPUT_LIMIT
        );
        embed.insert(1, b' ');
        assert_eq!(
            decode_input(InputMode::EmbeddingLine, &embed)
                .unwrap_err()
                .site,
            CodecSite::InputSize
        );
        for mode in [InputMode::RankLine, InputMode::EmbeddingLine] {
            assert_eq!(
                decode_input(mode, b"\xff").unwrap_err().site,
                CodecSite::MissingLineFeed
            );
            assert_eq!(
                decode_input(mode, b"0").unwrap_err().site,
                CodecSite::MissingLineFeed
            );
            let read = decoded(mode, b"0\n\xff\n");
            assert_eq!(read.consumed(), 2);
            assert_eq!(read.value(), &integer(false, vec![]));
            // Byte LF, before any Unicode decoder. LE is truncated here.
            family(
                mode,
                &hex("fffe30000a00"),
                ExceptionFamily::UnicodeDecodeError,
            );
            family(
                mode,
                &hex("300000000a000000"),
                ExceptionFamily::UnicodeDecodeError,
            );
            assert_eq!(
                decoded(mode, &hex("0030000a")).value(),
                &integer(false, vec![])
            );
            assert_eq!(
                decoded(mode, &hex("000000300000000a")).value(),
                &integer(false, vec![])
            );
            // Byte readline selects 00220a. CPython detect_encoding falls
            // back to UTF8 at unmarked length 3; initial U+0000 fails JSON.
            family(
                mode,
                &hex("00220a000022000a"),
                ExceptionFamily::JsonDecodeError,
            );
            // The BOM instead forces UTF16BE for prefix feff00220a; its
            // three-byte payload has an incomplete code unit before JSON.
            family(
                mode,
                &hex("feff00220a000022000a"),
                ExceptionFamily::UnicodeDecodeError,
            );
        }
    }

    #[test]
    fn sdp3_whole_selected_unicode_conversion_precedes_json_and_depth() {
        for input in [b"!\xff".as_slice(), b"0 \xff", b"[!\xff"] {
            family(
                InputMode::RankOnce,
                input,
                ExceptionFamily::UnicodeDecodeError,
            );
        }
        let mut deep = vec![b'['; 65];
        deep.push(0xff);
        family(
            InputMode::RankOnce,
            &deep,
            ExceptionFamily::UnicodeDecodeError,
        );
        family(
            InputMode::RankLine,
            b"!\xff\n",
            ExceptionFamily::UnicodeDecodeError,
        );
        assert_eq!(
            decoded(InputMode::RankLine, b"null\n\xff").value(),
            &Value::Null
        );
        family(
            InputMode::RankLine,
            b"!\n\xff",
            ExceptionFamily::JsonDecodeError,
        );
    }

    #[test]
    fn sdp3_encoding_detection_boms_nul_prefixes_and_nonleading_bom() {
        for fixture in [
            "30",
            "efbbbf30",
            "3000",
            "0030",
            "30000000",
            "00000030",
            "fffe3000",
            "feff0030",
            "fffe000030000000",
            "0000feff00000030",
        ] {
            assert_eq!(value(&hex(fixture)), integer(false, vec![]), "{fixture}");
        }
        family(
            InputMode::RankOnce,
            &hex("20efbbbf30"),
            ExceptionFamily::JsonDecodeError,
        );
        family(
            InputMode::RankOnce,
            &hex("efbbbfefbbbf30"),
            ExceptionFamily::JsonDecodeError,
        );
        family(
            InputMode::RankOnce,
            &hex("fffe300000"),
            ExceptionFamily::UnicodeDecodeError,
        );
        family(
            InputMode::RankOnce,
            &hex("0000feff0000003000"),
            ExceptionFamily::UnicodeDecodeError,
        );
    }

    #[test]
    fn sdp3_utf16_literal_surrogatepass_families_with_and_without_bom() {
        let cases = [
            ("22003dd800de2200", vec![0x1f600], "fffe"),
            ("220000dc2200", vec![0xdc00], "fffe"),
            ("220000d861002200", vec![0xd800, 0x61], "fffe"),
            ("220000d82200", vec![0xd800], "fffe"),
            ("0022d83dde000022", vec![0x1f600], "feff"),
            ("0022dc000022", vec![0xdc00], "feff"),
            ("0022d80000610022", vec![0xd800, 0x61], "feff"),
            ("0022d8000022", vec![0xd800], "feff"),
        ];
        for (raw, expected, bom) in cases {
            assert_eq!(string_codepoints(&hex(raw)), expected, "{raw}");
            assert_eq!(string_codepoints(&hex(&format!("{bom}{raw}"))), expected);
        }
        for (raw, bom, expected) in [
            ("220000d8", "fffe", ExceptionFamily::JsonDecodeError),
            ("0022d800", "feff", ExceptionFamily::JsonDecodeError),
            (
                "220000dc220000",
                "fffe",
                ExceptionFamily::UnicodeDecodeError,
            ),
            (
                "0022dc00002200",
                "feff",
                ExceptionFamily::UnicodeDecodeError,
            ),
        ] {
            family(InputMode::RankOnce, &hex(raw), expected);
            family(InputMode::RankOnce, &hex(&format!("{bom}{raw}")), expected);
        }
        assert_eq!(unicode(&hex("fffe220000d8")).unwrap(), &[0x22, 0xd800]);
    }

    #[test]
    fn sdp3_utf32_literal_surrogate_units_pairs_and_truncated_suffixes() {
        for (raw, expected, bom) in [
            ("2200000000d8000022000000", vec![0xd800], "fffe0000"),
            ("2200000000dc000022000000", vec![0xdc00], "fffe0000"),
            (
                "2200000000d8000000dc000022000000",
                vec![0xd800, 0xdc00],
                "fffe0000",
            ),
            ("000000220000d80000000022", vec![0xd800], "0000feff"),
            ("000000220000dc0000000022", vec![0xdc00], "0000feff"),
            (
                "000000220000d8000000dc0000000022",
                vec![0xd800, 0xdc00],
                "0000feff",
            ),
        ] {
            for prefix in ["", bom] {
                let full = hex(&format!("{prefix}{raw}"));
                assert_eq!(string_codepoints(&full), expected);
                for suffix in ["00", "0000", "000000"] {
                    family(
                        InputMode::RankOnce,
                        &hex(&format!("{prefix}{raw}{suffix}")),
                        ExceptionFamily::UnicodeDecodeError,
                    );
                }
            }
        }
        for (raw, bom) in [
            ("220000000000110022000000", "fffe0000"),
            ("000000220011000000000022", "0000feff"),
        ] {
            family(
                InputMode::RankOnce,
                &hex(raw),
                ExceptionFamily::UnicodeDecodeError,
            );
            family(
                InputMode::RankOnce,
                &hex(&format!("{bom}{raw}")),
                ExceptionFamily::UnicodeDecodeError,
            );
        }
    }

    #[test]
    fn sdp3_utf8_literal_surrogatepass_and_strict_invalid_sequences() {
        for (raw, expected) in [
            ("22eda08022", vec![0xd800]),
            ("22edbfbf22", vec![0xdfff]),
            ("22eda080edb08022", vec![0xd800, 0xdc00]),
        ] {
            assert_eq!(string_codepoints(&hex(raw)), expected);
            assert_eq!(string_codepoints(&hex(&format!("efbbbf{raw}"))), expected);
        }
        for raw in [
            "22e0808022",
            "22eda0",
            "22c08022",
            "22f080808022",
            "22f490808022",
            "22e228a122",
        ] {
            family(
                InputMode::RankOnce,
                &hex(raw),
                ExceptionFamily::UnicodeDecodeError,
            );
            family(
                InputMode::RankOnce,
                &hex(&format!("efbbbf{raw}")),
                ExceptionFamily::UnicodeDecodeError,
            );
        }
    }

    #[test]
    fn sdp3_strings_escapes_strict_whitespace_and_surrogate_pair_distinctions() {
        assert_eq!(
            string_codepoints(br#""\"\\\/\b\f\n\r\t\u0000\u007f\uD800\u0061""#),
            vec![0x22, 0x5c, 0x2f, 8, 12, 10, 13, 9, 0, 0x7f, 0xd800, 0x61]
        );
        assert_eq!(string_codepoints(br#""\ud83d\ude00""#), vec![0x1f600]);
        assert_eq!(string_codepoints(br#""\ud800\udc00""#), vec![0x10000]);
        assert_eq!(
            string_codepoints(&hex("22eda080edb08022")),
            vec![0xd800, 0xdc00]
        );
        for raw in [
            br#""\x""#.as_slice(),
            br#""\u123""#,
            br#""\uZZZZ""#,
            b"\"a\nb\"",
            b"\"\x00\"",
            b"\"unclosed",
        ] {
            family(InputMode::RankOnce, raw, ExceptionFamily::JsonDecodeError);
        }
        assert_eq!(
            value(b" \t\r\n[true, false, null]\n"),
            Value::Array(vec![Value::Bool(true), Value::Bool(false), Value::Null])
        );
        family(
            InputMode::RankOnce,
            b"\x0bnull",
            ExceptionFamily::JsonDecodeError,
        );
        family(
            InputMode::RankOnce,
            "\u{a0}null".as_bytes(),
            ExceptionFamily::JsonDecodeError,
        );
        for raw in [
            b"[1,]".as_slice(),
            b"{\"a\":1,}",
            b"[",
            b"{a:1}",
            b"null true",
        ] {
            family(InputMode::RankOnce, raw, ExceptionFamily::JsonDecodeError);
        }
    }

    #[test]
    fn sdp3_duplicate_values_fold_last_first_position_and_ir_indices_stay_exact() {
        let result = value(br#"{"a":1,"b":2,"\u0061":3,"b":4}"#);
        assert_eq!(
            result,
            object(vec![
                ("a", integer(false, vec![3])),
                ("b", integer(false, vec![4]))
            ])
        );
        for (keys, first) in [(["a", "b", "a", "b"], 0), (["a", "b", "b", "a"], 1)] {
            assert_eq!(
                PyObject::new(keys.into_iter().map(|k| (k.into(), Value::Null)).collect())
                    .unwrap_err(),
                IrError::DuplicateObjectKey {
                    first,
                    duplicate: 2
                }
            );
        }
        let input = format!("{{\"a\":{},\"a\":0}}", "9".repeat(4301));
        assert_eq!(
            decode_input(InputMode::RankOnce, input.as_bytes())
                .unwrap_err()
                .site,
            CodecSite::IntegerDigits
        );
    }

    #[test]
    fn sdp3_integer_literal_limbs_signs_and_number_token_prefix_order() {
        for (token, negative, limbs) in [
            ("0", false, vec![]),
            ("-0", false, vec![]),
            ("9007199254740993", false, vec![1, 0x0020_0000]),
            ("-9007199254740993", true, vec![1, 0x0020_0000]),
            ("18446744073709551615", false, vec![u32::MAX, u32::MAX]),
            ("18446744073709551616", false, vec![0, 0, 1]),
            ("-18446744073709551617", true, vec![1, 0, 1]),
        ] {
            assert_eq!(value(token.as_bytes()), integer(negative, limbs), "{token}");
        }
        for raw in [
            b"01".as_slice(),
            b"1e",
            b"1e+",
            b"1.",
            b"-.1",
            b"+1",
            b"--1",
        ] {
            family(InputMode::RankOnce, raw, ExceptionFamily::JsonDecodeError);
        }
        for suffix in ["e", ".", "x"] {
            assert_eq!(
                decode_input(
                    InputMode::RankOnce,
                    format!("{}{suffix}", "9".repeat(4301)).as_bytes()
                )
                .unwrap_err()
                .site,
                CodecSite::IntegerDigits
            );
        }
        family(
            InputMode::RankOnce,
            format!("0{}", "9".repeat(4301)).as_bytes(),
            ExceptionFamily::JsonDecodeError,
        );
    }

    #[test]
    fn sdp3_decoder_signed_digit_limit_and_float_token_exemption() {
        let positive = format!("1{}", "0".repeat(4299));
        assert_eq!(value(positive.as_bytes()), integer(false, power10(4299)));
        assert_eq!(
            value(format!("-{positive}").as_bytes()),
            integer(true, power10(4299))
        );
        for sign in ["", "-"] {
            assert_eq!(
                decode_input(
                    InputMode::RankOnce,
                    format!("{sign}{}", "9".repeat(4301)).as_bytes()
                )
                .unwrap_err()
                .site,
                CodecSite::IntegerDigits
            );
        }
        let float = format!("1{}e-4300", "0".repeat(4300));
        assert_eq!(
            value(float.as_bytes()),
            Value::FloatBits(0x3ff0_0000_0000_0000)
        );
        assert_eq!(
            value(format!("1e{}", "9".repeat(4301)).as_bytes()),
            Value::FloatBits(0x7ff0_0000_0000_0000)
        );
    }

    #[test]
    fn sdp3_decimal_to_binary64_literal_bits_halfway_underflow_and_overflow() {
        for (token, bits) in [
            ("0.1", 0x3fb9_9999_9999_999a),
            ("-0.0", 0x8000_0000_0000_0000),
            ("-0e0", 0x8000_0000_0000_0000),
            ("5e-324", 1),
            ("2.2250738585072014e-308", 0x0010_0000_0000_0000),
            ("1.7976931348623157e308", 0x7fef_ffff_ffff_ffff),
            ("1e999", 0x7ff0_0000_0000_0000),
            ("-1e999", 0xfff0_0000_0000_0000),
            ("1e-999", 0),
            ("-1e-999", 0x8000_0000_0000_0000),
            (
                "1.00000000000000011102230246251565404236316680908203125",
                0x3ff0_0000_0000_0000,
            ),
            (
                "1.000000000000000111022302462515654042363166809082031251",
                0x3ff0_0000_0000_0001,
            ),
            (
                "1.00000000000000033306690738754696212708950042724609375",
                0x3ff0_0000_0000_0002,
            ),
        ] {
            assert_eq!(value(token.as_bytes()), Value::FloatBits(bits), "{token}");
        }
    }

    #[test]
    fn sdp3_nonfinite_literals_normalize_nan_and_strict_profiles_refuse() {
        for (token, bits, spelling) in [
            ("NaN", 0x7ff8_0000_0000_0000, "NaN"),
            ("Infinity", 0x7ff0_0000_0000_0000, "Infinity"),
            ("-Infinity", 0xfff0_0000_0000_0000, "-Infinity"),
        ] {
            assert_eq!(value(token.as_bytes()), Value::FloatBits(bits));
            assert_eq!(
                encoded(
                    encode(
                        RenderInput::Value(&Value::FloatBits(bits)),
                        RenderProfile::CacheValue
                    )
                    .unwrap()
                ),
                spelling.as_bytes()
            );
        }
        for raw in [
            b"-NaN".as_slice(),
            b"+Infinity",
            b"nan",
            b"inf",
            b"infinity",
        ] {
            family(InputMode::RankOnce, raw, ExceptionFamily::JsonDecodeError);
        }
        for bits in [
            0x7ff8_0000_0000_0000,
            0xfff0_0000_0000_0001,
            0x7ff0_0000_0000_0000,
            0xfff0_0000_0000_0000,
        ] {
            let input = Value::FloatBits(bits);
            assert_eq!(
                encode_rank_once(&input).unwrap_err(),
                value_error(CodecSite::NonFinite)
            );
            assert_eq!(
                encode_embedding_reply(&input).unwrap_err(),
                value_error(CodecSite::NonFinite)
            );
        }
        assert_eq!(count(&Value::FloatBits(0xfff8_1234_5678_9012)), 3);
    }

    #[test]
    fn sdp3_renderer_literal_float_notation_thresholds_and_shortest_digits() {
        for (bits, expected) in [
            (0.1f64.to_bits(), "0.1\n"),
            (1e-5f64.to_bits(), "1e-05\n"),
            (1e-4f64.to_bits(), "0.0001\n"),
            (1e15f64.to_bits(), "1000000000000000.0\n"),
            (1e16f64.to_bits(), "1e+16\n"),
            (1.0f64.to_bits(), "1.0\n"),
            (0x8000_0000_0000_0000, "-0.0\n"),
            (1, "5e-324\n"),
            (0x0010_0000_0000_0000, "2.2250738585072014e-308\n"),
            (0x7fef_ffff_ffff_ffff, "1.7976931348623157e+308\n"),
            (0x4310_0000_0000_0001, "1125899906842624.2\n"),
        ] {
            assert_eq!(
                once(&Value::FloatBits(bits)),
                expected.as_bytes(),
                "{bits:016x}"
            );
        }
    }

    #[test]
    fn sdp3_renderer_literal_ascii_escaping_order_and_compact_separators() {
        let escaped = Value::String(
            PyString::from_codepoints(vec![
                0x22, 0x5c, 8, 12, 10, 13, 9, 0x1f, 0x7f, 0xd800, 0x1f600, 0x3c, 0x3e, 0x26, 0x2f,
            ])
            .unwrap(),
        );
        let expected = b"\"\\\"\\\\\\b\\f\\n\\r\\t\\u001f\\u007f\\ud800\\ud83d\\ude00<>&/\"\n";
        assert_eq!(once(&escaped), expected);
        assert_eq!(count(&escaped), expected.len() as u128 - 1);
        let input = object(vec![
            (
                "z",
                Value::Array(vec![
                    Value::Bool(true),
                    Value::FloatBits(0x8000_0000_0000_0000),
                ]),
            ),
            (
                "a",
                Value::String(PyString::from_codepoints(vec![0x7f]).unwrap()),
            ),
        ]);
        assert_eq!(once(&input), b"{\"z\": [true, -0.0], \"a\": \"\\u007f\"}\n");
        assert_eq!(
            encoded(encode_embedding_reply(&input).unwrap()),
            b"{\"z\":[true,-0.0],\"a\":\"\\u007f\"}\n"
        );
        // Source itself is not an IR identity roundtrip for a literal pair.
        let literal_pair = Value::String(PyString::from_codepoints(vec![0xd800, 0xdc00]).unwrap());
        assert_eq!(once(&literal_pair), b"\"\\ud800\\udc00\"\n");
        assert_eq!(string_codepoints(&once(&literal_pair)), vec![0x10000]);
    }

    #[test]
    fn sdp3_shared_counter_matches_bytes_and_independent_literal_lengths() {
        for (input, expected) in [
            (Value::Null, 4),
            (Value::Bool(true), 4),
            (Value::Bool(false), 5),
            (Value::FloatBits(0), 3),
            (Value::FloatBits(0x8000_0000_0000_0000), 4),
            (Value::FloatBits(1), 6),
            (integer(false, vec![0, 0, 1]), 20),
            (integer(true, vec![0, 0, 1]), 21),
            (Value::FloatBits(0x7ff0_0000_0000_0000), 8),
            (Value::FloatBits(0xfff0_0000_0000_0000), 9),
        ] {
            assert_eq!(count(&input), expected);
            assert_eq!(cache::measure_envelope(&input).unwrap(), expected);
        }
        for depth in 0..=64 {
            assert_eq!(count(&nested(depth)), 4 + 2 * depth as u128);
        }
        for i in 0..32 {
            let input = object(vec![
                ("n", integer(i % 2 == 1, vec![i + 1, u32::MAX])),
                (
                    "s",
                    Value::String(
                        PyString::from_codepoints(vec![0x100 + i, 0x7f, 0x1f600]).unwrap(),
                    ),
                ),
                ("a", nested((i % 5) as usize)),
            ]);
            let bytes =
                encoded(encode(RenderInput::Value(&input), RenderProfile::CacheValue).unwrap());
            assert_eq!(count(&input), bytes.len() as u128);
        }
    }

    #[test]
    fn sdp3_encoder_integer_4300_negative_4300_and_4301_are_distinct() {
        let magnitude = power10(4299);
        assert_eq!(magnitude.len(), 447); // 447 is not a blanket refusal.
        let positive = once(&integer(false, magnitude));
        assert_eq!(positive.len(), 4301);
        assert_eq!(positive[0], b'1');
        assert!(positive[1..4300].iter().all(|&b| b == b'0'));
        assert_eq!(positive[4300], b'\n');
        let negative = once(&integer(true, all_nines(4300)));
        assert_eq!(negative.len(), 4302);
        assert_eq!(negative[0], b'-');
        assert!(negative[1..4301].iter().all(|&b| b == b'9'));
        assert_eq!(negative[4301], b'\n');
        let too_large = integer(false, power10(4300));
        assert_eq!(
            encode_rank_once(&too_large).unwrap_err(),
            value_error(CodecSite::IntegerDigits)
        );
        let after_large = Value::Array(vec![
            text(&"A".repeat(EMBEDDING_REPLY_LIMIT + 1)),
            too_large,
        ]);
        assert_eq!(
            encode_embedding_reply(&after_large).unwrap_err(),
            value_error(CodecSite::IntegerDigits)
        );
    }

    #[test]
    fn sdp3_count_integer_4300_negative_4300_and_4301_fail_before_cache_identity() {
        assert_eq!(count(&integer(false, power10(4299))), 4300);
        assert_eq!(count(&integer(true, all_nines(4300))), 4301);
        let too_large = integer(false, power10(4300));
        assert_eq!(
            measure_cache_value(&too_large).unwrap_err(),
            value_error(CodecSite::IntegerDigits)
        );
        for model in [text("other"), text(&"A".repeat(614401))] {
            let input = object(vec![
                ("format", text("cairn.passage-vectors/1")),
                ("model_sha256", model),
                ("vectors", too_large.clone()),
            ]);
            let mut old = cache::CacheState::new();
            let before = old.clone();
            assert_eq!(
                measure_cache_value(&input).unwrap_err(),
                value_error(CodecSite::IntegerDigits)
            );
            let error = cache::restore_payload(
                &mut old,
                &input,
                &cache::SuppliedIdentity::new("self".into()),
            )
            .unwrap_err();
            assert_eq!(
                error.site,
                cache::CacheSite::EnvelopeConversion(CodecSite::IntegerDigits)
            );
            assert_eq!(error.kind, FailureKind::ValueError);
            assert_eq!(old, before);
        }
    }

    #[test]
    fn sdp3_normalized_448_limb_guard_precedes_any_full_decimal_conversion() {
        for length in [448, 4096] {
            let mut limbs = vec![0; length];
            *limbs.last_mut().unwrap() = 1;
            let input = integer(false, limbs);
            assert_eq!(
                encode_rank_once(&input).unwrap_err().site,
                CodecSite::IntegerDigits
            );
            assert_eq!(
                measure_cache_value(&input).unwrap_err().site,
                CodecSite::IntegerDigits
            );
        }
        // Exact 447-limb conversion remains required; literal 4300-digit
        // fixtures above accept, while 10^4300 (also 447 limbs) refuses.
        assert_eq!(power10(4300).len(), 447);
    }

    #[test]
    fn sdp3_depth_pending_retains_original_profile_and_first_failure_order() {
        let raw = format!("{}null{}", "[".repeat(64), "]".repeat(64));
        assert_eq!(value(raw.as_bytes()), nested(64));
        let deep = format!("{}null{}", "[".repeat(65), "]".repeat(65));
        let DecodeOutcome::NeedsSourceDepth(pending) =
            decode_input(InputMode::RankOnce, deep.as_bytes()).unwrap()
        else {
            panic!("depth pending")
        };
        assert!(std::ptr::eq(pending.input(), deep.as_bytes()));
        assert_eq!(pending.mode(), InputMode::RankOnce);
        assert_eq!(pending.profile(), DecodeProfile::SourceBytesDefault4300);
        family(
            InputMode::RankOnce,
            format!("[!,{deep}]").as_bytes(),
            ExceptionFamily::JsonDecodeError,
        );
        assert_eq!(
            decode_input(
                InputMode::RankOnce,
                format!("[{},{}]", "9".repeat(4301), deep).as_bytes()
            )
            .unwrap_err()
            .site,
            CodecSite::IntegerDigits
        );
        assert!(matches!(
            decode_input(InputMode::RankOnce, format!("[{deep},!]").as_bytes()).unwrap(),
            DecodeOutcome::NeedsSourceDepth(_)
        ));
        let mut line = deep.as_bytes().to_vec();
        line.extend_from_slice(b"\n\xff");
        let DecodeOutcome::NeedsSourceDepth(pending) =
            decode_input(InputMode::RankLine, &line).unwrap()
        else {
            panic!("line depth pending")
        };
        assert_eq!(pending.consumed(), deep.len() + 1);
        assert!(std::ptr::eq(pending.input(), line.as_slice()));
        let input = nested(65);
        let EncodeOutcome::NeedsSourceDepth(pending) = encode_rank_once(&input).unwrap() else {
            panic!("encode depth pending")
        };
        assert_eq!(pending.profile(), RenderProfile::RankOnce);
        let RenderInput::Value(original) = pending.input() else {
            panic!("original value")
        };
        assert!(std::ptr::eq(original, &input));
        assert!(matches!(
            measure_cache_value(&input).unwrap(),
            MeasureOutcome::NeedsSourceDepth(_)
        ));
        let huge = integer(false, power10(4300));
        let early = Value::Array(vec![huge.clone(), nested(64)]);
        assert_eq!(
            encode_rank_once(&early).unwrap_err().site,
            CodecSite::IntegerDigits
        );
        assert_eq!(
            measure_cache_value(&early).unwrap_err().site,
            CodecSite::IntegerDigits
        );
        let later = Value::Array(vec![nested(64), huge]);
        assert!(matches!(
            encode_rank_once(&later).unwrap(),
            EncodeOutcome::NeedsSourceDepth(_)
        ));
        assert!(matches!(
            measure_cache_value(&later).unwrap(),
            MeasureOutcome::NeedsSourceDepth(_)
        ));
    }

    #[test]
    fn sdp3_cache_full_numeric_and_depth_conversion_precedes_bound_and_identity() {
        let identity = cache::SuppliedIdentity::new("self".into());
        let mut old = cache::CacheState::new();
        for vectors in [
            Value::FloatBits(0x3fb9_9999_9999_999a),
            integer(false, vec![0, 0, 1]),
            nested(63),
        ] {
            let input = object(vec![
                ("format", text("cairn.passage-vectors/1")),
                ("model_sha256", text("other")),
                ("vectors", vectors),
            ]);
            assert_eq!(
                cache::restore_payload(&mut old, &input, &identity).unwrap(),
                cache::RestoreOutcome::IgnoredOtherModel
            );
        }
        let input = object(vec![
            ("format", text("cairn.passage-vectors/1")),
            ("model_sha256", text(&"A".repeat(614401))),
            ("vectors", nested(64)),
        ]);
        let cache::RestoreOutcome::NeedsSourceMeasure(pending) =
            cache::restore_payload(&mut old, &input, &identity).unwrap()
        else {
            panic!("cache depth pending")
        };
        assert!(std::ptr::eq(pending.input(), &input));
        assert_eq!(pending.profile(), RenderProfile::CacheValue);
        assert!(old.entries().is_empty());
    }

    #[test]
    fn sdp3_reply_byte_caps_include_lf_and_conversion_precedes_size() {
        let id = PyString::from("x");
        let baseline = b"{\"id\": \"x\", \"result\": \"\"}\n";
        assert_eq!(baseline.len(), 26);
        let at = text(&"A".repeat(RANK_REPLY_LIMIT - 26));
        assert_eq!(ordinary(&id, &at).bytes().len(), 65536);
        let over = text(&"A".repeat(RANK_REPLY_LIMIT - 25));
        assert_eq!(
            prepare_rank_stream_reply(&id, &over).unwrap_err().site,
            CodecSite::OrdinaryRankSize
        );
        let embed = text(&"A".repeat(EMBEDDING_REPLY_LIMIT - 3));
        assert_eq!(
            encoded(encode_embedding_reply(&embed).unwrap()).len(),
            4 * 1024 * 1024
        );
        assert_eq!(
            encode_embedding_reply(&text(&"A".repeat(EMBEDDING_REPLY_LIMIT - 2)))
                .unwrap_err()
                .site,
            CodecSite::EmbeddingSize
        );
        let null = Value::Null;
        let ordinary = ordinary(&id, &null);
        let cached_baseline = b"{\"id\": \"x\", \"result\": null, \"cache\": \"\"}\n";
        assert_eq!(cached_baseline.len(), 41);
        assert_eq!(
            encoded(
                ordinary
                    .encode_with_cache(&text(&"A".repeat(CACHED_RANK_REPLY_LIMIT - 41)))
                    .unwrap()
            )
            .len(),
            679936
        );
        assert_eq!(
            ordinary
                .encode_with_cache(&text(&"A".repeat(CACHED_RANK_REPLY_LIMIT - 40)))
                .unwrap_err()
                .site,
            CodecSite::CachedRankSize
        );
        let source_failure = Value::Array(vec![
            text(&"A".repeat(CACHED_RANK_REPLY_LIMIT + 1)),
            Value::FloatBits(0x7ff8_0000_0000_0001),
        ]);
        assert_eq!(
            ordinary
                .encode_with_cache(&source_failure)
                .unwrap_err()
                .site,
            CodecSite::NonFinite
        );
        assert_eq!(
            prepare_rank_stream_reply(&id, &source_failure)
                .unwrap_err()
                .site,
            CodecSite::NonFinite
        );
        assert!(once(&text(&"A".repeat(65536))).len() > 65536); // No oneshot Source cap.
    }

    #[test]
    fn sdp3_ordinary_reply_record_reuses_exact_id_result_and_appends_cache_last() {
        let id = PyString::from("x");
        let result = object(vec![("a", integer(false, vec![1]))]);
        let record = ordinary(&id, &result);
        assert!(std::ptr::eq(record.id(), &id));
        assert!(std::ptr::eq(record.result(), &result));
        assert_eq!(record.bytes(), b"{\"id\": \"x\", \"result\": {\"a\": 1}}\n");
        assert_eq!(
            encoded(
                record
                    .encode_with_cache(&object(vec![("k", Value::Bool(true))]))
                    .unwrap()
            ),
            b"{\"id\": \"x\", \"result\": {\"a\": 1}, \"cache\": {\"k\": true}}\n"
        );
        assert_eq!(
            prepare_rank_stream_reply(&id, &Value::FloatBits(0x7ff0_0000_0000_0000))
                .unwrap_err()
                .site,
            CodecSite::NonFinite
        );
        assert!(matches!(
            prepare_rank_stream_reply(&id, &nested(64)).unwrap(),
            RankStreamPreparation::NeedsSourceDepth(_)
        ));
        assert!(matches!(
            prepare_rank_stream_reply(&id, &nested(63)).unwrap(),
            RankStreamPreparation::Ordinary(_)
        ));
    }

    #[test]
    fn sdp3_raw_codecs_feed_existing_guards_without_worker_or_tokenizer_admission() {
        let request=value(br#"{"query":"q","notes":[{"body":"body","body_sha256":"230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5","version":18446744073709551616}]}"#);
        assert_eq!(
            hash::validate_rank_hashes(&request)
                .unwrap()
                .remaining_work(),
            hash::RemainingRankWork::NeedsRankTokenizer
        );
        let scalar = value(b"0");
        assert_eq!(
            crate::validate_rank_prefix(&scalar).unwrap_err().site,
            Site::RankNotesLookup
        );
        let bad_hash = value(br#"{"query":"q","notes":[{"body":"abc","body_sha256":null}]}"#);
        assert_eq!(
            hash::validate_rank_hashes(&bad_hash).unwrap_err().site,
            Site::RankBodyHashMismatch(0)
        );
        let surrogate_body =
            value(br#"{"query":"q","notes":[{"body":"\ud800","body_sha256":null}]}"#);
        assert_eq!(
            hash::validate_rank_hashes(&surrogate_body).unwrap_err(),
            crate::Refusal {
                site: Site::RankBodyEncode(0),
                kind: FailureKind::UnicodeEncodeError,
            }
        );
        let empty=value(br#"{"query":"q","notes":[{"body":"","body_sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]}"#);
        assert_eq!(
            hash::validate_rank_hashes(&empty).unwrap().remaining_work(),
            hash::RemainingRankWork::NeedsRankTokenizer
        );
        let embed = value(br#"{"id":"e","operation":"query","text":"hello"}"#);
        assert!(matches!(
            crate::validate_embed_prefix(&embed).unwrap(),
            EmbedPrefix::NeedsTokenizer(_)
        ));
        let envelope = object(vec![
            ("id", text("s")),
            ("request", request),
            ("cache", Value::Null),
        ]);
        let admitted = hash::validate_stream_hashes(
            &envelope,
            CachePolicy::AdvertisedCache,
            hash::StreamPhase::Fresh,
        )
        .unwrap();
        assert_eq!(
            cache::restore_stream_cache(
                &mut cache::CacheState::new(),
                &admitted,
                &cache::SuppliedIdentity::new("self".into())
            )
            .unwrap(),
            cache::RestoreOutcome::NoOp
        );
        assert_eq!(
            hash::validate_stream_hashes(
                &envelope,
                CachePolicy::AdvertisedCache,
                hash::StreamPhase::Active
            )
            .unwrap_err()
            .site,
            Site::StreamLateCache
        );
        assert_eq!(
            hash::validate_stream_hashes(&envelope, CachePolicy::NoCache, hash::StreamPhase::Fresh)
                .unwrap_err()
                .site,
            Site::StreamKeys
        );
    }
}
