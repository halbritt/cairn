// Owned synthetic inputs and independent literal span expectations only.
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

fn passage(text: &str, offset: usize, length: usize) -> DocumentPassage {
    DocumentPassage {
        text: text.to_owned(),
        span: Utf8Span { offset, length },
    }
}

fn offsets(points: Vec<(usize, usize)>) -> Result<DocumentEncoding, Error> {
    Ok(DocumentEncoding {
        count: points.len(),
        offsets: points,
    })
}

fn count_only(count: usize) -> Result<DocumentEncoding, Error> {
    Ok(DocumentEncoding {
        count,
        offsets: vec![],
    })
}

fn scripted(
    text: &str,
    observations: Vec<Result<DocumentEncoding, Error>>,
) -> (
    Result<Vec<DocumentPassage>, DocumentSpansError>,
    Vec<(String, bool)>,
) {
    let mut observations = observations.into_iter();
    let mut calls = Vec::new();
    let result = project_document(&PyString::from(text), |text, special| {
        calls.push((text.to_owned(), special));
        observations.next().expect("unexpected extra encoding")
    });
    assert!(observations.next().is_none(), "unused scripted observation");
    (result, calls)
}

// One token per Python character; no window/span algorithm in this fixture.
fn characters(text: &str) -> Vec<DocumentPassage> {
    project_document(&PyString::from(text), |text, special| {
        let n = text.chars().count();
        Ok(DocumentEncoding {
            count: n + if special { 2 } else { 0 },
            offsets: if special {
                vec![]
            } else {
                (0..n).map(|i| (i, i + 1)).collect()
            },
        })
    })
    .unwrap()
}

#[test]
fn document_spans3_short_text_has_exact_owned_text_and_byte_span() {
    assert_eq!(
        prepared()
            .document_passages(&PyString::from("abc"))
            .unwrap(),
        [passage("abc", 0, 3)]
    );
}

#[test]
fn document_spans3_ascii_windows_keep_every_final_short_tail() {
    let tokenizer = prepared();
    let cases: &[(usize, &[(usize, usize)])] = &[
        (96, &[(0, 191)]),
        (97, &[(0, 193), (192, 1)]),
        (127, &[(0, 253), (192, 61)]),
        (128, &[(0, 255), (192, 63)]),
        (129, &[(0, 255), (192, 65)]),
        (192, &[(0, 255), (192, 191)]),
        (193, &[(0, 255), (192, 193), (384, 1)]),
    ];
    for &(words, expected) in cases {
        let body = "a ".repeat(words);
        let result = tokenizer
            .document_passages(&PyString::from(body.as_str()))
            .unwrap();
        let actual: Vec<_> = result
            .iter()
            .map(|p| (p.span.offset, p.span.length))
            .collect();
        assert_eq!(actual, expected, "{words} original tokens");
        for (p, &(start, length)) in result.iter().zip(expected) {
            assert_eq!(p.text, &body[start..start + length]);
        }
    }
}

#[test]
fn document_spans3_unicode_spans_do_not_expand_ignored_geometry() {
    let body = " \u{200b} é漢🙂 e\u{0301} \u{200b} ";
    let result = prepared().document_passages(&PyString::from(body)).unwrap();
    assert_eq!(result, [passage("é漢🙂 e", 5, 11)]);
    assert_eq!(&body.as_bytes()[5..16], "é漢🙂 e".as_bytes());
    // Leading ignored bytes and the trailing combining mark stay outside.
}

#[test]
fn document_spans3_original_case_spaces_and_punctuation_are_not_decoded() {
    let result = prepared()
        .document_passages(&PyString::from("  HELLO   worlds! \t"))
        .unwrap();
    assert_eq!(result, [passage("HELLO   worlds!", 2, 15)]);
}

#[test]
fn document_spans3_python_blank_scalars_refuse_before_encoding() {
    for point in [
        9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197,
        8198, 8199, 8200, 8201, 8202, 8232, 8233, 8239, 8287, 12288,
    ] {
        let text = PyString::from_codepoints(vec![point; 2]).unwrap();
        let error =
            project_document(&text, |_, _| panic!("blank must precede encoding")).unwrap_err();
        assert_eq!(error.source_kind(), Some(FailureKind::ValueError));
        assert!(matches!(error, DocumentSpansError::InputBounds));
    }
    assert!(matches!(
        project_document(&PyString::from(""), |_, _| panic!()),
        Err(DocumentSpansError::InputBounds)
    ));
}

#[test]
fn document_spans3_lone_surrogates_keep_unicode_error_family_and_position() {
    for (points, index, codepoint) in [(vec![0xd800], 0, 0xd800), (vec![97, 0xdfff], 1, 0xdfff)] {
        let error = project_document(&PyString::from_codepoints(points).unwrap(), |_, _| {
            panic!("UTF8 before encoding")
        })
        .unwrap_err();
        assert_eq!(error.source_kind(), Some(FailureKind::UnicodeEncodeError));
        assert!(
            matches!(error,DocumentSpansError::Encoding(Error::TextUtf8(source)) if source.index==index && source.codepoint==codepoint)
        );
    }
}

#[test]
fn document_spans3_nonblank_token_empty_text_has_distinct_refusal() {
    let tokenizer = prepared();
    for body in ["\u{200b}", "\0", "\u{0301}"] {
        assert!(matches!(
            tokenizer.document_passages(&PyString::from(body)),
            Err(DocumentSpansError::NoModelTokens)
        ));
    }
    let (result, calls) = scripted("abc", vec![offsets(vec![])]);
    assert!(matches!(result, Err(DocumentSpansError::NoModelTokens)));
    assert_eq!(calls, [("abc".into(), false)]);
}

#[test]
fn document_spans3_65536_utf8_bytes_are_inclusive_without_model_truncation() {
    let tokenizer = prepared();
    for body in ["a".repeat(65536), "é".repeat(32768), "🙂".repeat(16384)] {
        assert_eq!(body.len(), 65536);
        let result = tokenizer
            .document_passages(&PyString::from(body.as_str()))
            .unwrap();
        assert_eq!(result, [passage(&body, 0, 65536)]);
    }
}

#[test]
fn document_spans3_over_byte_limit_refuses_before_any_tokenizer_call() {
    for body in [
        "a".repeat(65537),
        "é".repeat(32768) + "a",
        "🙂".repeat(16384) + "a",
    ] {
        let result = project_document(&PyString::from(body.as_str()), |_, _| {
            panic!("byte guard before encoding")
        });
        assert!(matches!(result, Err(DocumentSpansError::InputBounds)));
    }
}

#[test]
fn document_spans3_no_instruction_and_default_special_token_policies() {
    let (result, calls) = scripted("abc", vec![offsets(vec![(0, 3)]), count_only(512)]);
    assert_eq!(result.unwrap(), [passage("abc", 0, 3)]);
    assert_eq!(calls, [("abc".into(), false), ("abc".into(), true)]);
}

#[test]
fn document_spans3_window_endpoints_are_first_and_last_not_maximum() {
    let (result, calls) = scripted(
        "abcdefghij",
        vec![offsets(vec![(2, 9), (0, 5), (4, 6)]), count_only(4)],
    );
    assert_eq!(result.unwrap(), [passage("cdef", 2, 4)]);
    assert_eq!(calls[1], ("cdef".into(), true));
}

#[test]
fn document_spans3_equal_or_reversed_endpoints_refuse_without_reencoding() {
    for points in [vec![(3, 3)], vec![(4, 2)], vec![(5, 9), (1, 5)]] {
        let (result, calls) = scripted("abcdefghij", vec![offsets(points)]);
        assert!(matches!(result, Err(DocumentSpansError::InvalidOffsets)));
        assert_eq!(calls.len(), 1);
    }
}

#[test]
fn document_spans3_late_bad_span_precedes_any_passage_retokenization() {
    let mut points: Vec<_> = (0..129).map(|i| (i, i + 1)).collect();
    points[128] = (128, 96); // first window valid; second has last==first
    let (result, calls) = scripted(&"a".repeat(129), vec![offsets(points)]);
    assert!(matches!(result, Err(DocumentSpansError::InvalidOffsets)));
    assert_eq!(calls.len(), 1);
}

#[test]
fn document_spans3_python_slice_clipping_adds_no_offset_refusal_policy() {
    let (result, _) = scripted("é漢🙂", vec![offsets(vec![(1, 999)]), count_only(4)]);
    assert_eq!(result.unwrap(), [passage("漢🙂", 2, 7)]);
    let (result, _) = scripted("é漢🙂", vec![offsets(vec![(10, 20)]), count_only(2)]);
    assert_eq!(result.unwrap(), [passage("", 9, 0)]);
}

#[test]
fn document_spans3_window_population_uses_offsets_not_id_count() {
    let original = DocumentEncoding {
        count: 0,
        offsets: (0..129).map(|i| (i, i + 1)).collect(),
    };
    let (result, _) = scripted(
        &"a".repeat(129),
        vec![Ok(original), count_only(130), count_only(35)],
    );
    assert_eq!(
        result.unwrap(),
        [
            passage(&"a".repeat(128), 0, 128),
            passage(&"a".repeat(33), 96, 33)
        ]
    );
}

#[test]
fn document_spans3_adaptive_transition_has_literal_window_and_tail_expectations() {
    let fixed = characters(&"a".repeat(24576));
    assert_eq!(fixed.len(), 256);
    assert_eq!(
        fixed[0].span,
        Utf8Span {
            offset: 0,
            length: 128
        }
    );
    assert_eq!(
        fixed[1].span,
        Utf8Span {
            offset: 96,
            length: 128
        }
    );
    assert_eq!(
        fixed.last().unwrap().span,
        Utf8Span {
            offset: 24480,
            length: 96
        }
    );
    let adaptive = characters(&"a".repeat(24577));
    assert_eq!(adaptive.len(), 254);
    assert_eq!(
        adaptive[0].span,
        Utf8Span {
            offset: 0,
            length: 129
        }
    );
    assert_eq!(
        adaptive[1].span,
        Utf8Span {
            offset: 97,
            length: 129
        }
    );
    assert_eq!(
        adaptive.last().unwrap().span,
        Utf8Span {
            offset: 24541,
            length: 36
        }
    );
}

#[test]
fn document_spans3_maximum_density_keeps_256_overlapping_passages_and_tail() {
    let body = "a".repeat(65536);
    let result = characters(&body);
    assert_eq!(result.len(), 256);
    assert_eq!(
        result[0].span,
        Utf8Span {
            offset: 0,
            length: 288
        }
    );
    assert_eq!(
        result[1].span,
        Utf8Span {
            offset: 256,
            length: 288
        }
    );
    assert_eq!(
        result[255].span,
        Utf8Span {
            offset: 65280,
            length: 256
        }
    );
    for p in result {
        assert_eq!(p.text, body[p.span.offset..p.span.offset + p.span.length]);
    }
}

#[test]
fn document_spans3_window_arithmetic_avoids_full_usize_overflow() {
    assert_eq!(window_shape(24576), (96, 128));
    assert_eq!(window_shape(24577), (97, 129));
    assert_eq!(window_shape(32768), (128, 160));
    assert_eq!(window_shape(65536), (256, 288));
    #[cfg(target_pointer_width = "64")]
    assert_eq!(
        window_shape(usize::MAX),
        (72057594037927936, 72057594037927968)
    );
}

#[test]
fn document_spans3_each_passage_512_is_inclusive_and_513_never_truncates() {
    let points: Vec<_> = (0..97).map(|i| (i, i + 1)).collect();
    let (result, calls) = scripted(
        &"a".repeat(97),
        vec![offsets(points.clone()), count_only(512), count_only(512)],
    );
    assert_eq!(result.unwrap().len(), 2);
    assert_eq!(calls.len(), 3);
    let (result, calls) = scripted(
        &"a".repeat(97),
        vec![offsets(points.clone()), count_only(512), count_only(513)],
    );
    assert!(matches!(result, Err(DocumentSpansError::PassageTokenBound)));
    assert_eq!(calls.len(), 3);
    let (result, calls) = scripted(&"a".repeat(97), vec![offsets(points), count_only(513)]);
    assert!(matches!(result, Err(DocumentSpansError::PassageTokenBound)));
    assert_eq!(calls.len(), 2); // stop at first failing passage
}

#[test]
fn document_spans3_zero_retokenized_count_is_not_a_new_special_case() {
    let (result, _) = scripted("abc", vec![offsets(vec![(0, 3)]), count_only(0)]);
    assert_eq!(result.unwrap(), [passage("abc", 0, 3)]);
}

#[test]
fn document_spans3_original_and_late_encode_errors_retain_their_causes() {
    let (result, calls) = scripted("abc", vec![Err(Error::EncodingBatchCount(17))]);
    assert_eq!(result.as_ref().unwrap_err().source_kind(), None);
    assert!(matches!(
        result,
        Err(DocumentSpansError::Encoding(Error::EncodingBatchCount(17)))
    ));
    assert_eq!(calls.len(), 1);
    let points = (0..97).map(|i| (i, i + 1)).collect();
    let (result, calls) = scripted(
        &"a".repeat(97),
        vec![
            offsets(points),
            count_only(4),
            Err(Error::EncodingBatchCount(47)),
        ],
    );
    assert!(matches!(
        result,
        Err(DocumentSpansError::Encoding(Error::EncodingBatchCount(47)))
    ));
    assert_eq!(calls.len(), 3);
}

#[test]
fn document_spans3_real_wordpiece_retokenization_can_change_window_count() {
    let tokenizer = prepared();
    let body = "worldssss ".repeat(30);
    let result = tokenizer
        .document_passages(&PyString::from(body.as_str()))
        .unwrap();
    let first = "worldssss ".repeat(25) + "worldss";
    let second = "ssss ".to_owned() + &"worldssss ".repeat(9) + "worldssss";
    assert_eq!(
        result,
        [passage(&first, 0, 257), passage(&second, 195, 104)]
    );
    assert_eq!(
        tokenizer
            .rank_char_encoding(&result[0].text, true)
            .unwrap()
            .get_ids()
            .len(),
        130
    );
    // 54 original tokens in second window become51 + CLS/SEP after the cut.
    assert_eq!(
        tokenizer
            .rank_char_encoding(&result[1].text, true)
            .unwrap()
            .get_ids()
            .len(),
        53
    );
}
