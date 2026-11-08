//! Ordered body hashes after the existing decoded prefix. These capabilities
//! do not establish a tokenizer, initialized model, score or worker admission.

use sha2::{Digest, Sha256};
use std::fmt::Write;

use crate::{
    lookup, refusal, validate_rank_prefix, validate_stream_prefix, CachePolicy, FailureKind,
    PyString, RankPendingHashes, Refusal, Site, StreamPendingHashes, Value,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RemainingRankWork {
    NeedsRankTokenizer,
}

/// The original prefix plus actual ordered body-hash comparisons. Private
/// fields prevent a caller from asserting hash acceptance with a boolean.
#[derive(Debug)]
pub struct HashAdmittedRank<'a> {
    prefix: RankPendingHashes<'a>,
}

impl<'a> HashAdmittedRank<'a> {
    pub fn prefix(&self) -> &RankPendingHashes<'a> {
        &self.prefix
    }
    pub fn remaining_work(&self) -> RemainingRankWork {
        RemainingRankWork::NeedsRankTokenizer
    }
}

pub fn admit_rank_hashes(prefix: RankPendingHashes<'_>) -> Result<HashAdmittedRank<'_>, Refusal> {
    for (index, (body, note)) in prefix.body_utf8().iter().zip(prefix.notes()).enumerate() {
        // Compute each hash, including repeated bodies, BEFORE its lookup.
        let digest = Sha256::digest(body);
        let mut hex = String::with_capacity(64);
        for byte in digest {
            write!(&mut hex, "{byte:02x}").expect("formatting into String cannot fail");
        }
        let expected = lookup(note, "body_sha256", Site::RankExpectedHashLookup(index))?;
        if !matches!(expected, Value::String(text) if text.equals_str(&hex)) {
            return Err(refusal(
                Site::RankBodyHashMismatch(index),
                FailureKind::ValueError,
            ));
        }
    }
    Ok(HashAdmittedRank { prefix })
}

/// Runs the existing prefix exactly once, then consumes its retained bytes.
pub fn validate_rank_hashes(input: &Value) -> Result<HashAdmittedRank<'_>, Refusal> {
    admit_rank_hashes(validate_rank_prefix(input)?)
}

/// Supplied policy input only; neither variant attests to a real scorer/process.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StreamPhase {
    Fresh,
    Active,
}

#[derive(Debug)]
pub struct HashAdmittedStream<'a> {
    input: &'a Value,
    id: &'a PyString,
    rank: HashAdmittedRank<'a>,
    cache: Option<&'a Value>,
    policy: CachePolicy,
    phase: StreamPhase,
}

impl<'a> HashAdmittedStream<'a> {
    pub fn input(&self) -> &'a Value {
        self.input
    }
    pub fn id(&self) -> &'a PyString {
        self.id
    }
    pub fn rank(&self) -> &HashAdmittedRank<'a> {
        &self.rank
    }
    pub fn cache(&self) -> Option<&'a Value> {
        self.cache
    }
    pub fn policy(&self) -> CachePolicy {
        self.policy
    }
    pub fn phase(&self) -> StreamPhase {
        self.phase
    }
}

pub fn admit_stream_hashes(
    pending: StreamPendingHashes<'_>,
    phase: StreamPhase,
) -> Result<HashAdmittedStream<'_>, Refusal> {
    let StreamPendingHashes {
        input,
        id,
        rank,
        cache,
        policy,
    } = pending;
    let rank = admit_rank_hashes(rank)?;
    if phase == StreamPhase::Active && cache.is_some() {
        return Err(refusal(Site::StreamLateCache, FailureKind::ValueError));
    }
    Ok(HashAdmittedStream {
        input,
        id,
        rank,
        cache,
        policy,
        phase,
    })
}

pub fn validate_stream_hashes(
    input: &Value,
    policy: CachePolicy,
    phase: StreamPhase,
) -> Result<HashAdmittedStream<'_>, Refusal> {
    admit_stream_hashes(validate_stream_prefix(input, policy)?, phase)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{PyInteger, PyObject};

    const BODY: &str = "230d8358dc8e8890b4c58deeb62912ee2f20357ae92a5cc861b98e68fe31acb5";
    const ABC: &str = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    const EMPTY: &str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

    fn text(value: &str) -> Value {
        Value::String(value.into())
    }
    fn object(entries: Vec<(&str, Value)>) -> Value {
        Value::Object(
            PyObject::new(entries.into_iter().map(|(k, v)| (k.into(), v)).collect()).unwrap(),
        )
    }
    fn note(body: &str, expected: Option<Value>, version: Value) -> Value {
        let mut fields = vec![("body", text(body)), ("version", version)];
        if let Some(expected) = expected {
            fields.push(("body_sha256", expected));
        }
        object(fields)
    }
    fn rank(notes: Vec<Value>, query: Value) -> Value {
        object(vec![("query", query), ("notes", Value::Array(notes))])
    }
    fn envelope(request: Value, cache: Option<Value>) -> Value {
        let mut fields = vec![("id", text("protocol-rank")), ("request", request)];
        if let Some(cache) = cache {
            fields.push(("cache", cache));
        }
        object(fields)
    }
    fn good_rank() -> Value {
        rank(
            vec![note("body", Some(text(BODY)), Value::Null)],
            text("query"),
        )
    }

    #[test]
    fn sdp2_literal_hashes_preserve_original_order_versions_and_pending_tokenizer() {
        // SDP2-HASH: independently specified digests, not generated expectations.
        let input = rank(
            vec![
                note(
                    "body",
                    Some(text(BODY)),
                    Value::FloatBits(0xfff8_0000_0000_0001),
                ),
                note(
                    "abc",
                    Some(text(ABC)),
                    Value::Integer(PyInteger::new(true, vec![7, 9, 1])),
                ),
                note("", Some(text(EMPTY)), Value::Bool(false)),
            ],
            text("query"),
        );
        let admitted = validate_rank_hashes(&input).unwrap();
        assert!(std::ptr::eq(admitted.prefix().input(), &input));
        assert_eq!(
            admitted.prefix().body_utf8(),
            &[b"body".to_vec(), b"abc".to_vec(), vec![]]
        );
        let Value::Object(object) = &input else {
            panic!("fixture object")
        };
        let Some(Value::Array(notes)) = object.get_str("notes") else {
            panic!("fixture notes")
        };
        assert!(std::ptr::eq(admitted.prefix().notes(), notes.as_slice()));
        assert_eq!(
            admitted.remaining_work(),
            RemainingRankWork::NeedsRankTokenizer
        );
    }

    #[test]
    fn sdp2_hash_mismatch_uses_python_inequality_without_type_refusal() {
        for expected in [
            text(&BODY.to_uppercase()),
            text(ABC),
            Value::Null,
            Value::Bool(false),
            Value::Integer(PyInteger::new(false, vec![])),
            Value::FloatBits(0),
            Value::Array(vec![]),
            object(vec![]),
        ] {
            let input = rank(
                vec![note("body", Some(expected), Value::Null)],
                text("query"),
            );
            assert_eq!(
                validate_rank_hashes(&input).unwrap_err(),
                Refusal {
                    site: Site::RankBodyHashMismatch(0),
                    kind: FailureKind::ValueError,
                }
            );
        }
        let changed = rank(
            vec![note("abc", Some(text(BODY)), Value::Null)],
            text("query"),
        );
        assert_eq!(
            validate_rank_hashes(&changed).unwrap_err().site,
            Site::RankBodyHashMismatch(0)
        );
    }

    #[test]
    fn sdp2_missing_hash_and_first_failing_note_remain_ordered() {
        let missing = note("body", None, Value::Null);
        let mismatch = note("body", Some(text(ABC)), Value::Null);
        for (notes, site, kind) in [
            (
                vec![missing.clone(), mismatch.clone()],
                Site::RankExpectedHashLookup(0),
                FailureKind::KeyError,
            ),
            (
                vec![mismatch.clone(), missing.clone()],
                Site::RankBodyHashMismatch(0),
                FailureKind::ValueError,
            ),
            (
                vec![note("body", Some(text(BODY)), Value::Null), missing],
                Site::RankExpectedHashLookup(1),
                FailureKind::KeyError,
            ),
            (
                vec![note("body", Some(text(BODY)), Value::Null), mismatch],
                Site::RankBodyHashMismatch(1),
                FailureKind::ValueError,
            ),
        ] {
            assert_eq!(
                validate_rank_hashes(&rank(notes, text("q"))).unwrap_err(),
                Refusal { site, kind }
            );
        }
    }

    #[test]
    fn sdp2_existing_prefix_failures_precede_all_hash_and_cache_policy() {
        let bad_body = object(vec![(
            "body",
            Value::String(PyString::from_codepoints(vec![0xd800]).unwrap()),
        )]);
        let later_encoding = rank(vec![note("body", None, Value::Null), bad_body], text("q"));
        assert_eq!(
            validate_rank_hashes(&later_encoding).unwrap_err().site,
            Site::RankBodyEncode(1)
        );
        let blank = envelope(
            rank(vec![note("body", None, Value::Null)], text("")),
            Some(Value::Null),
        );
        assert_eq!(
            validate_stream_hashes(&blank, CachePolicy::AdvertisedCache, StreamPhase::Active)
                .unwrap_err()
                .site,
            Site::RankQueryGuard
        );
        let unadvertised = envelope(good_rank(), Some(Value::Null));
        assert_eq!(
            validate_stream_hashes(&unadvertised, CachePolicy::NoCache, StreamPhase::Active)
                .unwrap_err()
                .site,
            Site::StreamKeys
        );
    }

    #[test]
    fn sdp2_stream_late_restore_follows_hashes_and_distinguishes_present_null() {
        let absent = envelope(good_rank(), None);
        for phase in [StreamPhase::Fresh, StreamPhase::Active] {
            let admitted =
                validate_stream_hashes(&absent, CachePolicy::AdvertisedCache, phase).unwrap();
            assert_eq!(admitted.cache(), None);
            assert_eq!(admitted.phase(), phase);
            assert!(std::ptr::eq(admitted.input(), &absent));
            assert_eq!(admitted.id(), &PyString::from("protocol-rank"));
        }
        for cache in [Value::Null, object(vec![]), Value::Bool(false)] {
            let present = envelope(good_rank(), Some(cache));
            assert!(validate_stream_hashes(
                &present,
                CachePolicy::AdvertisedCache,
                StreamPhase::Fresh
            )
            .is_ok());
            assert_eq!(
                validate_stream_hashes(&present, CachePolicy::AdvertisedCache, StreamPhase::Active)
                    .unwrap_err()
                    .site,
                Site::StreamLateCache
            );
        }
        let bad = envelope(
            rank(vec![note("abc", Some(text(BODY)), Value::Null)], text("q")),
            Some(Value::Null),
        );
        assert_eq!(
            validate_stream_hashes(&bad, CachePolicy::AdvertisedCache, StreamPhase::Active)
                .unwrap_err()
                .site,
            Site::RankBodyHashMismatch(0)
        );
    }
}
