use crate::backend::BackendFactory;
use crate::rank::Engine;
use crate::{
    Failure, InvocationError, InvocationFailure, IoStage, PendingProfile, RequestScope, StageError,
};
use cairn_semantic_protocol::hash::{self, StreamPhase};
use cairn_semantic_protocol::json::{
    self, DecodeOutcome, EncodeOutcome, InputMode, RankStreamPreparation,
};
use cairn_semantic_protocol::{CachePolicy, Value};
use std::io::{self, BufRead, Read, Write};

const MAX_REQUEST: usize = 8 * 1024 * 1024;

fn stage_error(error: StageError, input: Vec<u8>, mode: InputMode) -> InvocationError {
    let failure = match error {
        StageError::Failure(failure) => InvocationFailure::SourceStage(failure),
        StageError::Pending(profile) => InvocationFailure::PendingDepth(profile),
    };
    InvocationError {
        failure,
        input,
        mode,
        written: 0,
    }
}

fn io_error(
    error: io::Error,
    stage: IoStage,
    input: Vec<u8>,
    mode: InputMode,
    written: usize,
) -> InvocationError {
    InvocationError {
        failure: InvocationFailure::Io { stage, error },
        input,
        mode,
        written,
    }
}

fn decoded(mode: InputMode, raw: &[u8]) -> Result<Option<Value>, StageError> {
    match json::decode_input(mode, raw).map_err(Failure::Codec)? {
        DecodeOutcome::Decoded(input) => Ok(Some(input.into_value())),
        DecodeOutcome::EndOfInput => Ok(None),
        DecodeOutcome::NeedsSourceDepth(marker) => Err(StageError::Pending(
            PendingProfile::Decode(marker.profile()),
        )),
    }
}

fn encoded(outcome: EncodeOutcome<'_>) -> Result<Vec<u8>, StageError> {
    match outcome {
        EncodeOutcome::Complete(bytes) => Ok(bytes),
        EncodeOutcome::NeedsSourceDepth(marker) => Err(StageError::Pending(
            PendingProfile::Render(marker.profile()),
        )),
    }
}

fn once_reply<F: BackendFactory>(
    engine: &mut Engine<F>,
    raw: &[u8],
) -> Result<Vec<u8>, StageError> {
    let request =
        decoded(InputMode::RankOnce, raw)?.expect("rank-once empty input is a codec refusal");
    let admitted = hash::validate_rank_hashes(&request).map_err(Failure::Prefix)?;
    engine.construct()?;
    let result = engine.score(&admitted)?;
    encoded(json::encode_rank_once(&result).map_err(Failure::Codec)?)
}

pub(crate) fn stream_reply<F: BackendFactory>(
    engine: &mut Engine<F>,
    raw: &[u8],
    policy: CachePolicy,
) -> Result<Option<Vec<u8>>, StageError> {
    let Some(envelope) = decoded(InputMode::RankLine, raw)? else {
        return Ok(None);
    };
    let phase = if engine.active() {
        StreamPhase::Active
    } else {
        StreamPhase::Fresh
    };
    // Includes hash checks before the existing-scorer late-cache refusal,
    // including a present null cache. Construction follows both checks.
    let admitted =
        hash::validate_stream_hashes(&envelope, policy, phase).map_err(Failure::Prefix)?;
    if !engine.active() {
        engine.construct()?;
        if policy == CachePolicy::AdvertisedCache {
            engine.restore(admitted.cache().unwrap_or(&Value::Null))?;
        }
    }
    let result = engine.score(admitted.rank())?;
    // Measure ordinary reply including LF BEFORE requesting any snapshot.
    match json::prepare_rank_stream_reply(admitted.id(), &result).map_err(Failure::Codec)? {
        RankStreamPreparation::NeedsSourceDepth(marker) => Err(StageError::Pending(
            PendingProfile::Render(marker.profile()),
        )),
        RankStreamPreparation::Ordinary(ordinary) => {
            if policy == CachePolicy::AdvertisedCache {
                let cache = engine.snapshot()?;
                encoded(ordinary.encode_with_cache(&cache).map_err(Failure::Codec)?).map(Some)
            } else {
                Ok(Some(ordinary.bytes().to_vec()))
            }
        }
    }
}

fn write_reply<W: Write>(output: &mut W, bytes: &[u8]) -> Result<(), (IoStage, io::Error, usize)> {
    let mut written = 0;
    while written < bytes.len() {
        match output.write(&bytes[written..]) {
            Ok(0) => {
                return Err((
                    IoStage::Write,
                    io::Error::new(io::ErrorKind::WriteZero, "reply write returned zero"),
                    written,
                ))
            }
            Ok(count) => written += count,
            Err(error) => return Err((IoStage::Write, error, written)),
        }
    }
    output
        .flush()
        .map_err(|error| (IoStage::Flush, error, written))
}

/// Read no more than the Source's 8 MiB + 1 selected bytes, then pass that
/// exact frame through the published snapshot codec. No backend is constructed
/// until decoding, prefix checks and hashes have succeeded.
pub fn run_rank_once<R: Read, W: Write, F: BackendFactory>(
    input: &mut R,
    output: &mut W,
    factory: F,
) -> Result<(), InvocationError> {
    let mode = InputMode::RankOnce;
    let mut raw = Vec::new();
    let mut buffer = [0; 8192];
    while raw.len() < MAX_REQUEST + 1 {
        let count = buffer.len().min(MAX_REQUEST + 1 - raw.len());
        match input.read(&mut buffer[..count]) {
            Ok(0) => break,
            Ok(count) => raw.extend_from_slice(&buffer[..count]),
            Err(error) => return Err(io_error(error, IoStage::Read, raw, mode, 0)),
        }
    }
    let _scope = RequestScope::new();
    let mut engine = Engine::new(factory);
    let response =
        once_reply(&mut engine, &raw).map_err(|error| stage_error(error, raw.clone(), mode))?;
    write_reply(output, &response)
        .map_err(|(stage, error, written)| io_error(error, stage, raw, mode, written))
}

/// Incremental readline: stop at the first LF, the Source byte cap or EOF.
/// In particular, do not wait for EOF after receiving a complete request line.
fn read_line<R: BufRead>(input: &mut R, raw: &mut Vec<u8>) -> io::Result<()> {
    while raw.len() < MAX_REQUEST + 2 {
        let available = input.fill_buf()?;
        if available.is_empty() {
            break;
        }
        let limit = available.len().min(MAX_REQUEST + 2 - raw.len());
        let selected = &available[..limit];
        let count = selected
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(limit, |position| position + 1);
        let ended = selected[count - 1] == b'\n';
        raw.extend_from_slice(&selected[..count]);
        input.consume(count);
        if ended {
            break;
        }
    }
    Ok(())
}

/// One owning scorer for the invocation. Every failure is terminal; there is
/// no next read, constructor retry, replacement backend or fallback. Request
/// references and reply bytes leave scope before waiting for the next line.
/// This ownership boundary is not an allocator-memory erasure claim.
pub fn run_rank_stream<R: BufRead, W: Write, F: BackendFactory>(
    input: &mut R,
    output: &mut W,
    factory: F,
    policy: CachePolicy,
) -> Result<(), InvocationError> {
    let mut engine = Engine::new(factory);
    loop {
        // Everything belonging to this request is scoped inside this loop
        // iteration. The engine retains only session state and copied raw cache.
        let mut raw = Vec::new();
        if let Err(error) = read_line(input, &mut raw) {
            return Err(io_error(error, IoStage::Read, raw, InputMode::RankLine, 0));
        }
        if raw.is_empty() {
            return Ok(());
        }
        let _scope = RequestScope::new();
        let response = stream_reply(&mut engine, &raw, policy)
            .map_err(|error| stage_error(error, raw.clone(), InputMode::RankLine))?;
        let Some(response) = response else {
            return Ok(());
        };
        write_reply(output, &response).map_err(|(stage, error, written)| {
            io_error(error, stage, raw, InputMode::RankLine, written)
        })?;
    }
}
