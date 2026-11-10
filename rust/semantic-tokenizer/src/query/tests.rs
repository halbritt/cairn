// Focused item2 tests; independent literal expectations and owned byte inputs.
use super::*;
use crate::PreparedInputs;

const TOKENIZER: &str = r###"{
 "version":"1.0","truncation":null,"padding":null,"added_tokens":[],
 "normalizer":{"type":"BertNormalizer","clean_text":true,"handle_chinese_chars":true,"strip_accents":true,"lowercase":true},
 "pre_tokenizer":{"type":"BertPreTokenizer"},
 "post_processor":{"type":"BertProcessing","sep":["[SEP]",3],"cls":["[CLS]",2]},
 "decoder":{"type":"WordPiece","prefix":"##","cleanup":true},
 "model":{"type":"WordPiece","unk_token":"[UNK]","continuing_subword_prefix":"##","max_input_chars_per_word":100,
 "vocab":{"[PAD]":0,"[UNK]":1,"[CLS]":2,"[SEP]":3,"a":4,"abc":5,"e":6,"漢":7,"hello":8,"world":9,"##s":10,":":11}}
}"###;

fn prepared() -> PreparedTokenizer {
    PreparedTokenizer::from_bytes(PreparedInputs {
        config: br#"{"pad_token_id":0}"#,
        tokenizer_config: br#"{"model_max_length":6,"pad_token":"[PAD]"}"#,
        special_tokens_map:
            br#"{"pad_token":"[PAD]","unk_token":"[UNK]","cls_token":"[CLS]","sep_token":"[SEP]"}"#,
        tokenizer: TOKENIZER.as_bytes(),
    })
    .unwrap()
}

fn original(count: usize, anchors: &[(usize, (usize, usize))]) -> ProjectionEncoding {
    let mut offsets = vec![(0, 0); count];
    for &(index, offset) in anchors {
        offsets[index] = offset;
    }
    ProjectionEncoding { count, offsets }
}

fn count_only(count: usize) -> Result<ProjectionEncoding, Error> {
    Ok(ProjectionEncoding {
        count,
        offsets: vec![],
    })
}

fn scripted(
    text: &str,
    observations: Vec<Result<ProjectionEncoding, Error>>,
) -> (Result<QueryProjection, QueryPrefixError>, Vec<String>) {
    let mut observations = observations.into_iter();
    let mut questions = Vec::new();
    let result = project_query(&PyString::from(text), |question| {
        questions.push(question.to_owned());
        observations.next().expect("unexpected additional encoding")
    });
    assert!(observations.next().is_none(), "unused scripted observation");
    (result, questions)
}

#[test]
fn query_prefix2_short_question_and_exact_metadata() {
    let result = prepared().query_prefix(&PyString::from("abc")).unwrap();
    assert_eq!(
        result.question,
        "Represent this sentence for searching relevant passages: abc"
    );
    assert_eq!(
        result.metadata,
        QueryProjectionMetadata {
            method: "original-prefix/1",
            truncated: false,
            original_tokens: 11,
            embedded_tokens: 11,
            prefix_bytes: 3,
            // Independent standard SHA-256 vector, without the instruction prefix.
            prefix_sha256: "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
                .into(),
        }
    );
}

#[test]
fn query_prefix2_exact512_preserves_text_and_full_rank_count() {
    let text = "a ".repeat(502);
    let result = prepared()
        .query_prefix(&PyString::from(text.as_str()))
        .unwrap();
    assert_eq!(
        result.question,
        format!("Represent this sentence for searching relevant passages: {text}")
    );
    assert!(!result.metadata.truncated);
    assert_eq!(
        (
            result.metadata.original_tokens,
            result.metadata.embedded_tokens
        ),
        (512, 512)
    );
    assert_eq!(result.metadata.prefix_bytes, 1004);
    // The fixture's model limit is six: a model_encoding substitution fails.
}

#[test]
fn query_prefix2_over512_keeps_original_count_and_original_prefix() {
    let text = "a ".repeat(503);
    let expected = "a ".repeat(501) + "a";
    let result = prepared()
        .query_prefix(&PyString::from(text.as_str()))
        .unwrap();
    assert_eq!(
        result.question,
        format!("Represent this sentence for searching relevant passages: {expected}")
    );
    assert!(result.metadata.truncated);
    assert_eq!(
        (
            result.metadata.original_tokens,
            result.metadata.embedded_tokens
        ),
        (513, 512)
    );
    assert_eq!(result.metadata.prefix_bytes, 1003);
}

#[test]
fn query_prefix2_utf8_byte_limit_is_inclusive() {
    let tokenizer = prepared();
    for text in ["a".repeat(4096), "é".repeat(2048), "🙂".repeat(1024)] {
        assert_eq!(text.len(), 4096);
        let result = tokenizer
            .query_prefix(&PyString::from(text.as_str()))
            .unwrap();
        assert_eq!(
            result.question,
            format!("Represent this sentence for searching relevant passages: {text}")
        );
        assert_eq!(result.metadata.prefix_bytes, 4096);
        let over = text + "a";
        let result = project_query(&PyString::from(over.as_str()), |_| {
            panic!("bounds must precede encoding")
        });
        assert!(matches!(result, Err(QueryPrefixError::InputBounds)));
    }
}

#[test]
fn query_prefix2_all_python_blank_scalars_refuse_before_encoding() {
    // Literal Python 3.11 set, independent of Rust trim/Unicode White_Space.
    for scalar in [
        9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197,
        8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288,
    ] {
        let text = PyString::from_codepoints(vec![scalar; 2]).unwrap();
        let result = project_query(&text, |_| panic!("blank must precede encoding"));
        assert_eq!(
            result.as_ref().unwrap_err().source_kind(),
            Some(FailureKind::ValueError)
        );
        assert!(matches!(result, Err(QueryPrefixError::InputBounds)));
    }
    assert!(matches!(
        project_query(&PyString::from(""), |_| panic!()),
        Err(QueryPrefixError::InputBounds)
    ));
}

#[test]
fn query_prefix2_nonblank_token_empty_input_is_not_rejected() {
    let tokenizer = prepared();
    for text in ["\u{200b}", "\0", "\u{0301}"] {
        let result = tokenizer.query_prefix(&PyString::from(text)).unwrap();
        assert_eq!(
            result.question,
            format!("Represent this sentence for searching relevant passages: {text}")
        );
        assert!(!result.metadata.truncated);
        assert_eq!(
            (
                result.metadata.original_tokens,
                result.metadata.embedded_tokens
            ),
            (10, 10)
        );
        assert_eq!(result.metadata.prefix_bytes, text.len());
    }
}

#[test]
fn query_prefix2_character_offsets_preserve_unicode_and_ignored_geometry() {
    let tokenizer = prepared();
    let text = "é漢🙂 e\u{0301} \u{200b} Z";
    let encoding = tokenizer.rank_char_encoding(text, true).unwrap();
    assert_eq!(encoding.get_ids(), [2, 6, 7, 1, 6, 1, 3]);
    assert_eq!(
        encoding.get_offsets(),
        [(0, 0), (0, 1), (1, 2), (2, 3), (4, 5), (9, 10), (0, 0)]
    );
    let original = OriginalText::new(text);
    assert_eq!(original.char_len(), 10);
    assert_eq!(original.slice(0..5), Some("é漢🙂 e"));
    assert_eq!(original.slice(0..5).unwrap().len(), 11);
    assert_eq!(original.slice(4..5), Some("e")); // no expansion over acute
    assert_eq!(original.slice(7..8), Some("\u{200b}")); // ignored bytes stay addressable
    assert_eq!(original.slice(2..3), Some("🙂")); // four UTF-8 bytes, one Python character
    assert_eq!(original.slice(10..10), Some(""));
    assert_eq!(original.slice(0..11), None);
}

#[test]
fn query_prefix2_multibyte_cuts_use_characters_not_bytes() {
    let tokenizer = prepared();
    for symbol in ["é", "漢", "🙂"] {
        let text = format!("{symbol} ").repeat(503);
        let expected = format!("{symbol} ").repeat(501) + symbol;
        let result = tokenizer
            .query_prefix(&PyString::from(text.as_str()))
            .unwrap();
        assert_eq!(
            result.question,
            format!("Represent this sentence for searching relevant passages: {expected}")
        );
        assert_eq!(
            (
                result.metadata.original_tokens,
                result.metadata.embedded_tokens
            ),
            (513, 512)
        );
        assert_eq!(
            result.metadata.prefix_bytes,
            501 * (symbol.len() + 1) + symbol.len()
        );
    }
}

#[test]
fn query_prefix2_cut_does_not_inflate_over_combining_marks() {
    let text = "e\u{0301} ".repeat(503);
    let expected = "e\u{0301} ".repeat(501) + "e";
    let result = prepared()
        .query_prefix(&PyString::from(text.as_str()))
        .unwrap();
    assert_eq!(
        result.question,
        format!("Represent this sentence for searching relevant passages: {expected}")
    );
    assert_eq!(
        (
            result.metadata.original_tokens,
            result.metadata.embedded_tokens
        ),
        (513, 512)
    );
    assert_eq!(result.metadata.prefix_bytes, 2005);
}

#[test]
fn query_prefix2_original_case_and_wordpiece_text_are_not_decoded() {
    let text = "HELLO worlds ".repeat(170);
    let expected = "HELLO worlds ".repeat(167) + "HELLO";
    let result = prepared()
        .query_prefix(&PyString::from(text.as_str()))
        .unwrap();
    assert_eq!(
        result.question,
        format!("Represent this sentence for searching relevant passages: {expected}")
    );
    assert_eq!(
        (
            result.metadata.original_tokens,
            result.metadata.embedded_tokens
        ),
        (520, 512)
    );
    assert_eq!(result.metadata.prefix_bytes, 2176);
    // A normalized/decoded representation loses HELLO's case and source spaces.
}

#[test]
fn query_prefix2_zero_length_specials_and_nonmonotonic_offsets_use_maximum() {
    let (result, calls) = scripted(
        "abcdef",
        vec![
            Ok(original(513, &[(2, (57, 60)), (200, (57, 59))])),
            count_only(77),
        ],
    );
    let result = result.unwrap();
    assert_eq!(
        calls,
        [
            "Represent this sentence for searching relevant passages: abcdef",
            "Represent this sentence for searching relevant passages: abc"
        ]
    );
    assert_eq!(result.metadata.original_tokens, 513);
    assert_eq!(result.metadata.embedded_tokens, 77);
    assert_eq!(result.metadata.prefix_bytes, 3);
    assert_eq!(
        result.metadata.prefix_sha256,
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
}

#[test]
fn query_prefix2_retokenization_retreat_uses_original_encoding() {
    let (result, calls) = scripted(
        "abcdefghij",
        vec![
            Ok(original(600, &[(507, (57, 63)), (510, (57, 65))])),
            count_only(515),
            count_only(512),
        ],
    );
    let result = result.unwrap();
    assert_eq!(
        calls,
        [
            "Represent this sentence for searching relevant passages: abcdefghij",
            "Represent this sentence for searching relevant passages: abcdefgh",
            "Represent this sentence for searching relevant passages: abcdef"
        ]
    );
    assert_eq!(result.question, calls[2]);
    assert_eq!(
        (
            result.metadata.original_tokens,
            result.metadata.embedded_tokens
        ),
        (600, 512)
    );
}

#[test]
fn query_prefix2_negative_slice_end_matches_python_instead_of_clamping_zero() {
    let (result, calls) = scripted(
        "abcdefghijkl",
        vec![
            Ok(original(
                600,
                &[(510, (57, 65)), (598, (57, 66)), (599, (57, 67))],
            )),
            count_only(1024),
            count_only(511),
        ],
    );
    // 511 - (1024 - 512) = -1: [: -1] includes offset598, excludes599.
    assert_eq!(
        calls[1],
        "Represent this sentence for searching relevant passages: abcdefgh"
    );
    assert_eq!(
        calls[2],
        "Represent this sentence for searching relevant passages: abcdefghi"
    );
    let result = result.unwrap();
    assert_eq!(result.metadata.prefix_bytes, 9);
    assert_eq!(result.metadata.embedded_tokens, 511);
}

#[test]
fn query_prefix2_eight_failed_retokenizations_are_the_entire_budget() {
    let mut observations = vec![Ok(original(600, &[(0, (57, 60))]))];
    observations.extend((0..8).map(|_| count_only(513)));
    let (result, calls) = scripted("abcdef", observations);
    assert!(matches!(result, Err(QueryPrefixError::TokenBound)));
    assert_eq!(calls.len(), 9); // original plus exactly eight re-encodings
}

#[test]
fn query_prefix2_blank_prefix_never_accepts_even_below_token_limit() {
    let mut observations = vec![Ok(original(600, &[(0, (57, 58))]))];
    observations.extend((0..8).map(|_| count_only(12)));
    let (result, calls) = scripted(" abc", observations);
    assert!(matches!(result, Err(QueryPrefixError::TokenBound)));
    assert_eq!(calls.len(), 9);
    assert!(calls[1..]
        .iter()
        .all(|s| s == "Represent this sentence for searching relevant passages:  "));
}

#[test]
fn query_prefix2_empty_prefix_and_invalid_lengths_refuse_without_reencoding() {
    for offsets in [
        vec![],
        vec![(0, 0); 513],
        vec![(0, 56); 513],
        vec![(0, 57); 513],
        vec![(0, 58); 513],
        vec![(0, 100); 513],
    ] {
        let (result, calls) = scripted(
            "a",
            vec![Ok(ProjectionEncoding {
                count: 513,
                offsets,
            })],
        );
        assert!(matches!(
            result,
            Err(QueryPrefixError::NoBoundedOriginalPrefix)
        ));
        assert_eq!(calls.len(), 1);
    }
}

#[test]
fn query_prefix2_encoding_errors_keep_the_original_cause_and_stop() {
    let (result, calls) = scripted("abcdef", vec![Err(Error::EncodingBatchCount(23))]);
    assert!(matches!(
        result,
        Err(QueryPrefixError::Encoding(Error::EncodingBatchCount(23)))
    ));
    assert_eq!(calls.len(), 1);
    let (result, calls) = scripted(
        "abcdef",
        vec![
            Ok(original(600, &[(0, (57, 60))])),
            Err(Error::EncodingBatchCount(29)),
        ],
    );
    assert!(matches!(
        result,
        Err(QueryPrefixError::Encoding(Error::EncodingBatchCount(29)))
    ));
    assert_eq!(calls.len(), 2);
}

#[test]
fn query_prefix2_strict_utf8_lone_surrogates_keep_protocol_failure_family() {
    for (points, index, codepoint) in [(vec![0xd800], 0, 0xd800), (vec![97, 0xdfff], 1, 0xdfff)] {
        let result = project_query(&PyString::from_codepoints(points).unwrap(), |_| {
            panic!("UTF8 must precede encoding")
        });
        let error = result.unwrap_err();
        assert_eq!(error.source_kind(), Some(FailureKind::UnicodeEncodeError));
        assert!(
            matches!(error, QueryPrefixError::Encoding(Error::TextUtf8(source)) if source.index==index && source.codepoint==codepoint)
        );
    }
}

#[test]
fn query_prefix2_signed_retreat_handles_large_counts_without_overflow() {
    let (result, calls) = scripted(
        "abcdef",
        vec![Ok(original(600, &[(0, (57, 60))])), count_only(usize::MAX)],
    );
    assert!(matches!(
        result,
        Err(QueryPrefixError::NoBoundedOriginalPrefix)
    ));
    assert_eq!(calls.len(), 2);
}

#[test]
fn query_prefix2_short_zero_token_observation_is_not_newly_refused() {
    let (result, calls) = scripted("abc", vec![count_only(0)]);
    let result = result.unwrap();
    assert_eq!(result.metadata.original_tokens, 0);
    assert_eq!(result.metadata.embedded_tokens, 0);
    assert!(!result.metadata.truncated);
    assert_eq!(calls.len(), 1);
}
