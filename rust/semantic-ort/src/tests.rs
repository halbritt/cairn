use crate::metadata::{extract_cls, Input, DIMENSIONS};
use crate::{ElementType, NodeMetadata, SessionMetadata};
use cairn_semantic_tokenizer::BatchOneTensors;

fn input(name: &str) -> NodeMetadata {
    NodeMetadata {
        name: name.into(),
        element_type: ElementType::Int64,
        dimensions: vec![-1, -1],
    }
}
fn metadata(names: &[&str]) -> SessionMetadata {
    SessionMetadata {
        inputs: names.iter().map(|n| input(n)).collect(),
        outputs: vec![NodeMetadata {
            name: "last_hidden_state".into(),
            element_type: ElementType::Float32,
            dimensions: vec![-1, -1, 384],
        }],
    }
}
fn batch(n: usize) -> BatchOneTensors {
    BatchOneTensors {
        shape: [1, n],
        input_ids: (0..n as i64).collect(),
        attention_mask: vec![1; n],
        token_type_ids: vec![0; n],
    }
}

#[test]
fn input_plan_follows_actual_session_order_and_optional_names() {
    let m = metadata(&["token_type_ids", "input_ids", "attention_mask"]);
    assert_eq!(
        m.validate().unwrap(),
        vec![Input::TokenTypeIds, Input::Ids, Input::AttentionMask]
    );
    assert_eq!(
        metadata(&["input_ids"]).validate().unwrap(),
        vec![Input::Ids]
    );
    assert_eq!(
        metadata(&["attention_mask", "input_ids"])
            .validate()
            .unwrap(),
        vec![Input::AttentionMask, Input::Ids]
    );
}
#[test]
fn mandatory_input_unknown_name_duplicate_and_dtype_errors() {
    for names in [
        &["attention_mask"][..],
        &["input_ids", "position_ids"][..],
        &["input_ids", "input_ids"][..],
    ] {
        assert!(metadata(names).validate().is_err(), "{names:?}");
    }
    let mut m = metadata(&["input_ids"]);
    m.inputs[0].element_type = ElementType::Float32;
    assert!(m.validate().is_err());
    m.inputs[0].element_type = ElementType::Other(10);
    assert!(m.validate().is_err());
}
#[test]
fn static_and_symbolic_dimensions_are_checked_against_each_owned_batch() {
    let mut m = metadata(&["input_ids", "attention_mask"]);
    assert_eq!(m.validate_batch(&batch(3)).unwrap(), 3);
    m.inputs[0].dimensions = vec![1, 3];
    assert!(m.validate_batch(&batch(3)).is_ok());
    assert!(m.validate_batch(&batch(4)).is_err());
    m.inputs[0].dimensions = vec![2, 3];
    assert!(m.validate().is_err());
    m.inputs[0].dimensions = vec![1, 0];
    assert!(m.validate().is_err());
    m.inputs[0].dimensions = vec![1, 3, 1];
    assert!(m.validate().is_err());
}
#[test]
fn owned_tensor_lengths_batch_shape_and_zero_type_ids_are_enforced() {
    let m = metadata(&["input_ids", "attention_mask", "token_type_ids"]);
    assert!(m.validate_batch(&batch(512)).is_ok());
    for n in [0, 513] {
        assert!(m.validate_batch(&batch(n)).is_err());
    }
    let mut b = batch(3);
    b.shape = [3, 1];
    assert!(m.validate_batch(&b).is_err());
    let mut b = batch(3);
    b.attention_mask.pop();
    assert!(m.validate_batch(&b).is_err());
    let mut b = batch(3);
    b.token_type_ids.pop();
    assert!(m.validate_batch(&b).is_err());
    let mut b = batch(3);
    b.token_type_ids[1] = 1;
    assert!(m.validate_batch(&b).is_err());
}
#[test]
fn first_output_is_selected_in_metadata_order_with_exact_type_rank_and_width() {
    let mut m = metadata(&["input_ids"]);
    m.outputs.insert(
        0,
        NodeMetadata {
            name: "pooler_output".into(),
            element_type: ElementType::Float32,
            dimensions: vec![1, 384],
        },
    );
    // Pinned FastEmbed onnx_text_model.py:18,96-98 requests run(None),
    // ORT Python inference_collection.py:323-326 expands metadata order,
    // then onnx_embedding.py:311-322 consumes [0], rank2 direct/rank3 CLS.
    // A later named/duplicate output cannot retarget the selected first one.
    assert!(m.validate().is_ok());
    assert_eq!(m.selected_output().unwrap(), &m.outputs[0]);
    m.outputs.push(m.outputs[1].clone());
    assert!(m.validate().is_ok());
    m.outputs[1].element_type = ElementType::Int64;
    m.outputs[1].dimensions = vec![2, 4, 383];
    m.outputs[1].name = "unselected_output".into();
    assert!(m.validate_batch(&batch(3)).is_ok());
    assert_eq!(m.selected_output().unwrap().name, "pooler_output");

    // Rejections apply to the selected first output even if a later valid
    // last_hidden_state is still advertised (the duplicate at index two).
    for element_type in [ElementType::Int64, ElementType::Other(10)] {
        m.outputs[0].element_type = element_type;
        assert!(m.validate().is_err());
    }
    m.outputs[0].element_type = ElementType::Float32;
    for shape in [
        vec![],
        vec![384],
        vec![1, 3, 384, 1],
        vec![2, 384],
        vec![1, 383],
        vec![2, 3, 384],
        vec![1, 3, 383],
        vec![1, 0, 384],
    ] {
        m.outputs[0].dimensions = shape;
        assert!(m.validate().is_err(), "{:?}", m.outputs[0]);
    }
    m.outputs[0].dimensions = vec![1, 4, 384];
    assert!(m.validate_batch(&batch(3)).is_err());
    m.outputs[0].dimensions = vec![1, 3, 384];
    assert_eq!(m.validate_batch(&batch(3)).unwrap(), 3);
    m.outputs[0].name = "another_first_output".into();
    assert!(m.validate().is_ok());
    assert_eq!(m.selected_output().unwrap().name, "another_first_output");
}

#[test]
fn cls_is_exact_first_token_bits_with_no_arithmetic_or_finiteness_rewrite() {
    let mut bits = vec![0x3f80_0000; 2 * DIMENSIONS];
    bits[..6].copy_from_slice(&[0, 0x8000_0000, 0x7fc0_1234, 0x7f80_0000, 0xff80_0000, 1]);
    bits[DIMENSIONS..].fill(0x42f6_0000);
    let cls = extract_cls(&[1, 2, 384], &bits, 2).unwrap();
    assert_eq!(
        &cls.0[..6],
        &[0, 0x8000_0000, 0x7fc0_1234, 0x7f80_0000, 0xff80_0000, 1]
    );
    assert_eq!(cls.0.as_slice(), &bits[..DIMENSIONS]);
    assert_ne!(cls.0.as_slice(), &bits[DIMENSIONS..]);
}
#[test]
fn cls_shape_element_count_and_empty_output_refuse_before_slicing() {
    let bits = vec![0; 2 * DIMENSIONS];
    for shape in [
        vec![2, 1, 384],
        vec![1, 1, 384],
        vec![1, 2, 383],
        vec![1, 2, 384, 1],
        vec![],
        vec![-1, 2, 384],
    ] {
        assert!(extract_cls(&shape, &bits, 2).is_err(), "{shape:?}");
    }
    assert!(extract_cls(&[1, 2, 384], &bits[..DIMENSIONS], 2).is_err());
    assert!(extract_cls(&[1, 0, 384], &[], 0).is_err());
    assert!(extract_cls(&[1, 513, 384], &[], 513).is_err());
}
