//! New Source relation tests use metadata/bits, never a native session/model.
use cairn_semantic_ort::metadata::{self, ElementType, NodeMetadata, SessionMetadata};
use cairn_semantic_tokenizer::BatchOneTensors;
fn node(name: &str, dimensions: &[i64]) -> NodeMetadata {
    NodeMetadata {
        name: name.into(),
        element_type: ElementType::Float32,
        dimensions: dimensions.to_vec(),
    }
}
fn metadata(outputs: Vec<NodeMetadata>) -> SessionMetadata {
    SessionMetadata {
        inputs: vec![NodeMetadata {
            name: "input_ids".into(),
            element_type: ElementType::Int64,
            dimensions: vec![1, -1],
        }],
        outputs,
    }
}
#[test]
fn p2_observe1_metadata_selects_source_first_not_later_named_output() {
    let m = metadata(vec![
        node("pooled-first", &[1, 384]),
        node("last_hidden_state", &[1, -1, 384]),
    ]);
    m.validate().unwrap();
    assert_eq!(m.selected_output().unwrap().name, "pooled-first");
    assert_eq!(m.outputs[1].name, "last_hidden_state");
    assert_eq!(m.selected_output().unwrap().dimensions, vec![1, 384]);
}
#[test]
fn p2_observe1_bad_first_output_cannot_be_rescued_by_valid_later_name() {
    let mut first = node("wrong-first", &[1, -1, 384]);
    first.element_type = ElementType::Int64;
    assert!(
        metadata(vec![first, node("last_hidden_state", &[1, -1, 384])])
            .validate()
            .is_err()
    );
    for dims in [
        vec![2, 384],
        vec![1, 383],
        vec![1, 0, 384],
        vec![1, 7, 385],
        vec![384],
    ] {
        assert!(metadata(vec![node("last_hidden_state", &dims)])
            .validate()
            .is_err());
    }
}
#[test]
fn p2_observe1_missing_and_duplicate_output_metadata_preserve_ordinal_relation() {
    assert!(metadata(vec![]).validate().is_err());
    let m = metadata(vec![
        node("last_hidden_state", &[1, -1, 384]),
        node("last_hidden_state", &[1, 17, 2]),
    ]);
    assert_eq!(m.selected_output().unwrap().dimensions, vec![1, -1, 384]);
    // Preserve duplicate advertised names/order; the ORT graph owns validity.
    assert_eq!(m.outputs.len(), 2);
    assert_eq!(m.selected_output().unwrap().name, "last_hidden_state");
}
#[test]
fn p2_observe1_rank2_and_rank3_raw_bits_are_not_mixed_or_mean_pooled() {
    let mut bits = vec![0x3f80_0000; 768];
    bits[383] = 0x7fc0_1234;
    bits[384..].fill(0x4000_0000);
    let cls = metadata::extract_cls(&[1, 2, 384], &bits, 2).unwrap();
    assert_eq!(cls.0[0], 0x3f80_0000);
    assert_eq!(cls.0[383], 0x7fc0_1234);
    assert_eq!(
        metadata::extract_cls(&[1, 384], &bits[..384], 2).unwrap(),
        cls
    );
    assert!(metadata::extract_cls(&[1, 2, 384], &bits[..384], 2).is_err());
    assert!(metadata::extract_cls(&[1, 384], &bits, 2).is_err());
}
#[test]
fn p2_observe1_fixed_sequence_checks_apply_to_rank3_not_rank2_width() {
    let batch = BatchOneTensors {
        shape: [1, 2],
        input_ids: vec![1, 2],
        attention_mask: vec![1, 1],
        token_type_ids: vec![0, 0],
    };
    assert_eq!(
        metadata(vec![node("first", &[1, 384])])
            .validate_batch(&batch)
            .unwrap(),
        2
    );
    assert_eq!(
        metadata(vec![node("first", &[1, 2, 384])])
            .validate_batch(&batch)
            .unwrap(),
        2
    );
    assert!(metadata(vec![node("first", &[1, 3, 384])])
        .validate_batch(&batch)
        .is_err());
}
