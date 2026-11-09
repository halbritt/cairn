//! Literal synthetic WordPiece fixtures. Tests exercise production preparation
//! and real crate Encoding; they are not the prepared294-input qualification.
use super::*;
use cairn_semantic_protocol::{FailureKind, PyString};
use cairn_semantic_rank::backend::{DecodeTokens, SpecialTokens};
use serde_json::{json, Value};

const TOKENIZER: &str = r###"{
 "version":"1.0","truncation":null,"padding":null,"added_tokens":[],
 "normalizer":{"type":"BertNormalizer","clean_text":true,"handle_chinese_chars":true,"strip_accents":null,"lowercase":true},
 "pre_tokenizer":{"type":"BertPreTokenizer"},
 "post_processor":{"type":"BertProcessing","sep":["[SEP]",3],"cls":["[CLS]",2]},
 "decoder":{"type":"WordPiece","prefix":"##","cleanup":true},
 "model":{"type":"WordPiece","unk_token":"[UNK]","continuing_subword_prefix":"##","max_input_chars_per_word":100,
 "vocab":{"[PAD]":0,"[UNK]":1,"[CLS]":2,"[SEP]":3,"[MASK]":4,"hello":5,"world":6,"##s":7,",":8,".":9,"cafe":10,"中":11,"play":12,"##ing":13,"e":14,"a":15}}
}"###;
const SPECIAL: &[u8] = br#"{"pad_token":"[PAD]","unk_token":"[UNK]","cls_token":"[CLS]","sep_token":"[SEP]","mask_token":"[MASK]"}"#;
const CONFIG: &[u8] = br#"{"pad_token_id":0}"#;
const TOKEN_CONFIG: &[u8] = br#"{"model_max_length":6,"pad_token":"[PAD]"}"#;

fn text(text: &str) -> PyString {
    PyString::from_codepoints(text.chars().map(u32::from).collect()).unwrap()
}
fn construct(
    tokenizer: &[u8],
    config: &[u8],
    token_config: &[u8],
    specials: &[u8],
) -> Result<PreparedTokenizer, Error> {
    PreparedTokenizer::from_bytes(PreparedInputs {
        tokenizer,
        config,
        tokenizer_config: token_config,
        special_tokens_map: specials,
    })
}
fn prepared() -> PreparedTokenizer {
    construct(TOKENIZER.as_bytes(), CONFIG, TOKEN_CONFIG, SPECIAL).unwrap()
}

/// load_tokenizer and rank clone: default special policy differs from body.
#[test]
fn rank_query_and_body_policies_have_literal_ids() {
    let tokenizer = prepared();
    assert_eq!(
        tokenizer
            .rank_encode(&text("Hello worlds"), SpecialTokens::Default)
            .unwrap(),
        [2, 5, 6, 7, 3]
    );
    assert_eq!(
        tokenizer
            .rank_encode(&text("Hello worlds"), SpecialTokens::Disabled)
            .unwrap(),
        [5, 6, 7]
    );
    assert_eq!(
        tokenizer
            .rank_decode(&[2, 5, 8, 6, 7, 9, 3], DecodeTokens::KeepSpecialTokens)
            .unwrap(),
        text("[CLS] hello, worlds. [SEP]")
    );
}

/// Source rank clone removes BOTH policies; model batch remains truncated.
#[test]
fn rank_clone_is_unbounded_while_model_truncation_stays_six() {
    let tokenizer = prepared();
    let input = text("hello hello hello hello hello hello hello");
    assert_eq!(
        tokenizer
            .rank_encode(&input, SpecialTokens::Default)
            .unwrap(),
        [2, 5, 5, 5, 5, 5, 5, 5, 3]
    );
    let model = tokenizer
        .model_tensors_in_order(&[input, text("world")])
        .unwrap();
    assert_eq!(
        model[0],
        BatchOneTensors {
            shape: [1, 6],
            input_ids: vec![2, 5, 5, 5, 5, 3],
            attention_mask: vec![1; 6],
            token_type_ids: vec![0; 6]
        }
    );
    assert_eq!(
        model[1],
        BatchOneTensors {
            shape: [1, 3],
            input_ids: vec![2, 6, 3],
            attention_mask: vec![1; 3],
            token_type_ids: vec![0; 3]
        }
    );
}

/// Existing model padding wins; its pad fields must not be spuriously read.
#[test]
fn existing_fixed_padding_is_preserved_only_on_model() {
    let mut fixture: Value = serde_json::from_str(TOKENIZER).unwrap();
    fixture["padding"] = json!({"strategy":{"Fixed":8},"direction":"Right","pad_to_multiple_of":null,"pad_id":0,"pad_type_id":7,"pad_token":"[PAD]"});
    let tokenizer = construct(
        &serde_json::to_vec(&fixture).unwrap(),
        b"null",
        br#"{"max_length":6}"#,
        SPECIAL,
    )
    .unwrap();
    assert_eq!(
        tokenizer
            .rank_encode(&text("hello"), SpecialTokens::Default)
            .unwrap(),
        [2, 5, 3]
    );
    let original = tokenizer.model_encoding(&text("hello")).unwrap();
    assert_eq!(original.get_type_ids(), [0, 0, 0, 7, 7, 7, 7, 7]);
    let converted = BatchOneTensors::from_encoding(&original).unwrap();
    assert_eq!(converted.token_type_ids, vec![0; 8]);
    assert_eq!(original.get_type_ids(), [0, 0, 0, 7, 7, 7, 7, 7]);
    let model = tokenizer.model_tensors_in_order(&[text("hello")]).unwrap();
    assert_eq!(
        model[0],
        BatchOneTensors {
            shape: [1, 8],
            input_ids: vec![2, 5, 3, 0, 0, 0, 0, 0],
            attention_mask: vec![1, 1, 1, 0, 0, 0, 0, 0],
            token_type_ids: vec![0; 8]
        }
    );
}

/// Both max keys select minimum; each one-key branch is independently usable.
#[test]
fn max_context_uses_each_key_or_the_minimum() {
    for config in [
        br#"{"max_length":4,"pad_token":"[PAD]"}"#.as_slice(),
        br#"{"model_max_length":4,"pad_token":"[PAD]"}"#,
        br#"{"max_length":4,"model_max_length":6,"pad_token":"[PAD]"}"#,
    ] {
        let tokenizer = construct(TOKENIZER.as_bytes(), CONFIG, config, SPECIAL).unwrap();
        assert_eq!(
            tokenizer
                .model_encoding(&text("hello world worlds"))
                .unwrap()
                .get_ids(),
            [2, 5, 6, 3]
        );
    }
    let tokenizer = construct(
        TOKENIZER.as_bytes(),
        CONFIG,
        br#"{"model_max_length":1000.5,"max_length":4,"pad_token":"[PAD]"}"#,
        SPECIAL,
    )
    .unwrap();
    assert_eq!(
        tokenizer
            .model_encoding(&text("hello world worlds"))
            .unwrap()
            .get_ids(),
        [2, 5, 6, 3]
    );
    // Python chooses the smaller float, then the usize bridge rejects it.
    // Rounding the integer to f64 first incorrectly keeps the integer.
    assert!(matches!(construct(TOKENIZER.as_bytes(), CONFIG,
        br#"{"model_max_length":9007199254740993,"max_length":9007199254740992.0,"pad_token":"[PAD]"}"#,
        SPECIAL), Err(Error::Configuration { file:"tokenizer_config.json", .. })));
}

/// Map insertion order determines newly allocated special IDs, not key sort.
#[test]
fn special_map_order_and_complete_added_token_flags_survive() {
    let special=br#"{"z_first":{"content":"<Z>","single_word":true,"lstrip":true,"rstrip":true,"normalized":false,"special":false},"a_second":"<A>","dict_default":{"content":"<D>"},"dict_explicit":{"content":"<N>","normalized":true}}"#;
    let tokenizer = construct(TOKENIZER.as_bytes(), CONFIG, TOKEN_CONFIG, special).unwrap();
    assert_eq!(
        tokenizer.special_token_ids(),
        &[
            ("<Z>".into(), Some(16)),
            ("<A>".into(), Some(17)),
            ("<D>".into(), Some(18)),
            ("<N>".into(), Some(19))
        ]
    );
    let model: Value = serde_json::from_str(&tokenizer.model_json().unwrap()).unwrap();
    let tokens = model["added_tokens"].as_array().unwrap();
    let z = tokens
        .iter()
        .find(|value| value["content"] == "<Z>")
        .unwrap();
    assert_eq!(
        z,
        &json!({"id":16,"content":"<Z>","single_word":true,"lstrip":true,"rstrip":true,"normalized":false,"special":true})
    );
    let d = tokens
        .iter()
        .find(|value| value["content"] == "<D>")
        .unwrap();
    assert_eq!(d["normalized"], false);
    assert_eq!(d["special"], true);
    let n = tokens
        .iter()
        .find(|value| value["content"] == "<N>")
        .unwrap();
    assert_eq!(n["normalized"], true);
    assert_eq!(n["special"], true);
    // 0.23.2 caches normalized added-token text for matching AND decoding
    // (AddedVocabulary::add_tokens/simple_id_to_token). Original content <N>
    // remains in metadata, but its normalized=true form is <n>; D stays raw.
    assert_eq!(
        tokenizer
            .rank_encode(&text("<D> <N> <n>"), SpecialTokens::Disabled)
            .unwrap(),
        [18, 19, 19]
    );
    assert_eq!(
        tokenizer
            .rank_decode(&[16, 17, 18, 19], DecodeTokens::KeepSpecialTokens)
            .unwrap(),
        text("<Z> <A> <D> <n>")
    );
}

/// Bert normalization/Chinese split, empty text and unknown Unicode stay real.
#[test]
fn empty_unicode_and_unknown_wordpiece_cases_are_literal() {
    let tokenizer = prepared();
    for (input, policy, expected) in [
        ("", SpecialTokens::Disabled, vec![]),
        ("", SpecialTokens::Default, vec![2, 3]),
        ("Café中", SpecialTokens::Default, vec![2, 10, 11, 3]),
        ("😀", SpecialTokens::Disabled, vec![1]),
        ("playing", SpecialTokens::Disabled, vec![12, 13]),
    ] {
        assert_eq!(
            tokenizer.rank_encode(&text(input), policy).unwrap(),
            expected
        );
    }
    let unpaired = PyString::from_codepoints(vec![0xd800]).unwrap();
    let error = tokenizer
        .rank_encode(&unpaired, SpecialTokens::Default)
        .unwrap_err();
    assert_eq!(error.source_kind(), Some(FailureKind::UnicodeEncodeError));
    assert!(matches!(
        tokenizer.model_encoding(&unpaired),
        Err(Error::TextUtf8(_))
    ));
}

/// Rank component does not steal the rank core's512-token admission policy.
#[test]
fn rank_encoding_adds_no_new_length_limit() {
    let tokenizer = prepared();
    let input = text(
        &std::iter::repeat_n("hello", 520)
            .collect::<Vec<_>>()
            .join(" "),
    );
    let ids = tokenizer
        .rank_encode(&input, SpecialTokens::Disabled)
        .unwrap();
    assert_eq!(ids, vec![5; 520]);
}

/// Input receipts retain all exact bytes and digests, not a ready/worker ID.
#[test]
fn consumed_inputs_are_exact_owned_copies() {
    let config = CONFIG.to_vec();
    let mut owned = config.clone();
    let tokenizer = construct(TOKENIZER.as_bytes(), &owned, TOKEN_CONFIG, SPECIAL).unwrap();
    owned.fill(b' ');
    assert_eq!(
        tokenizer
            .consumed_files()
            .iter()
            .map(|file| file.name)
            .collect::<Vec<_>>(),
        [
            "config.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "tokenizer.json"
        ]
    );
    assert_eq!(tokenizer.consumed_files()[0].bytes, config);
    use sha2::{Digest, Sha256};
    let expected: [u8; 32] = Sha256::digest(CONFIG).into();
    assert_eq!(tokenizer.consumed_files()[0].sha256, expected);
}

/// Ignored Source map branches/unknown kwargs are explicit preparation notices.
#[test]
fn ignored_special_branches_are_reported() {
    let tokenizer = construct(
        TOKENIZER.as_bytes(),
        CONFIG,
        TOKEN_CONFIG,
        br#"{"ignored":["<A>"],"extra":{"content":"<B>","unknown":true}}"#,
    )
    .unwrap();
    assert_eq!(
        tokenizer.notices(),
        &[
            PreparationNotice::IgnoredSpecialValue {
                name: "ignored".into()
            },
            PreparationNotice::IgnoredAddedTokenOption {
                name: "extra".into(),
                option: "unknown".into()
            }
        ]
    );
    assert_eq!(tokenizer.special_token_ids(), &[("<B>".into(), Some(16))]);
}

/// Failures traverse the production constructor; no parser or tokenizer mocks.
#[test]
fn malformed_inputs_and_config_branches_fail_explicitly() {
    assert!(matches!(
        construct(b"{", CONFIG, TOKEN_CONFIG, SPECIAL),
        Err(Error::Tokenizer {
            stage: "model tokenizer JSON",
            ..
        })
    ));
    assert!(matches!(
        construct(TOKENIZER.as_bytes(), b"{", TOKEN_CONFIG, SPECIAL),
        Err(Error::Json {
            file: "config.json",
            ..
        })
    ));
    for config in [
        br#"{"pad_token":"[PAD]"}"#.as_slice(),
        br#"{"max_length":"6","pad_token":"[PAD]"}"#,
        br#"{"max_length":6}"#,
    ] {
        assert!(matches!(
            construct(TOKENIZER.as_bytes(), CONFIG, config, SPECIAL),
            Err(Error::Configuration {
                file: "tokenizer_config.json",
                ..
            })
        ));
    }
    assert!(matches!(
        construct(
            TOKENIZER.as_bytes(),
            br#"{"pad_token_id":-1}"#,
            TOKEN_CONFIG,
            SPECIAL
        ),
        Err(Error::Configuration {
            file: "config.json",
            ..
        })
    ));
    assert!(matches!(
        construct(
            TOKENIZER.as_bytes(),
            CONFIG,
            TOKEN_CONFIG,
            br#"{"token":{"content":"x","lstrip":1}}"#
        ),
        Err(Error::Configuration {
            file: "special_tokens_map.json",
            ..
        })
    ));
    assert!(matches!(
        construct(TOKENIZER.as_bytes(), CONFIG, TOKEN_CONFIG, b"null"),
        Err(Error::Configuration {
            file: "special_tokens_map.json",
            ..
        })
    ));
}

/// Tensor conversion rejects a mismatched real Encoding object before output.
#[test]
fn mismatched_encoding_mask_refuses_without_partial_tensor() {
    let encoding = tokenizers::Encoding::new(
        vec![2, 3],
        vec![7; 2],
        vec!["[CLS]".into(), "[SEP]".into()],
        vec![None; 2],
        vec![(0, 0); 2],
        vec![1; 2],
        vec![1],
        vec![],
        Default::default(),
    );
    assert!(matches!(
        BatchOneTensors::from_encoding(&encoding),
        Err(Error::EncodingLengths { ids: 2, mask: 1 })
    ));
}
