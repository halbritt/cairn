//! Synthetic protobuf structures, not executable ONNX/ORT model fixtures.
use super::*;
fn var(mut value: u64) -> Vec<u8> {
    let mut out = Vec::new();
    loop {
        let low = (value & 127) as u8;
        value >>= 7;
        out.push(low | if value == 0 { 0 } else { 128 });
        if value == 0 {
            return out;
        }
    }
}
fn integer(field: u32, value: u64) -> Vec<u8> {
    let mut out = var(u64::from(field) << 3);
    out.extend(var(value));
    out
}
fn message(field: u32, body: &[u8]) -> Vec<u8> {
    let mut out = var((u64::from(field) << 3) | 2);
    out.extend(var(body.len() as u64));
    out.extend_from_slice(body);
    out
}
fn cat(parts: &[Vec<u8>]) -> Vec<u8> {
    parts.iter().flatten().copied().collect()
}
fn tensor(location: u64) -> Vec<u8> {
    cat(&[
        integer(1, 1),
        integer(2, 1),
        message(9, &[0, 0, 0, 0]),
        integer(14, location),
    ])
}
fn model(graph: &[u8]) -> Vec<u8> {
    cat(&[integer(1, 13), message(7, graph)])
}
fn node(attribute: &[u8]) -> Vec<u8> {
    message(5, attribute)
}
fn tensor_graph(tensor: &[u8]) -> Vec<u8> {
    message(5, tensor)
}
#[test]
fn p2_observe1_closure_binds_exact_consumed_model_bytes() {
    let bytes = model(&tensor_graph(&tensor(0)));
    let proof = observe(&bytes).unwrap();
    let bound = proof.bind(&bytes).unwrap();
    assert_eq!(bound["tensors"], 1);
    assert_eq!(bound["ir_version"], 13);
    let mut changed = bytes.clone();
    changed.extend(integer(5, 1));
    assert!(proof.bind(&changed).is_err());
}
#[test]
fn p2_observe1_external_tensors_are_found_in_every_schema_reachable_path() {
    let external = tensor(1);
    let attr = message(5, &external);
    let nested = message(1, &node(&attr));
    let sparse = cat(&[message(1, &tensor(0)), message(2, &external)]);
    let cases = vec![
        model(&message(5, &external)),
        model(&message(15, &sparse)),
        model(&message(1, &node(&message(5, &external)))),
        model(&message(1, &node(&message(10, &external)))),
        model(&message(1, &node(&message(6, &nested)))),
        model(&message(1, &node(&message(11, &nested)))),
        model(&message(1, &node(&message(22, &sparse)))),
        model(&message(1, &node(&message(23, &sparse)))),
        cat(&[model(&[]), message(20, &message(1, &nested))]),
        cat(&[model(&[]), message(20, &message(2, &nested))]),
        cat(&[model(&[]), message(25, &message(7, &node(&attr)))]),
        cat(&[model(&[]), message(25, &message(11, &attr))]),
    ];
    for bytes in cases {
        assert!(matches!(
            observe(&bytes),
            Err(Pending::ExternalTensor { .. })
        ));
    }
}
#[test]
fn p2_observe1_singular_tensor_messages_merge_and_last_scalar_wins() {
    // Two occurrences of Attribute.t merge. DEFAULT in the final fragment
    // overrides EXTERNAL in the first; the reverse order must remain pending.
    let first = tensor(1);
    let last = integer(14, 0);
    let merged = cat(&[message(5, &first), message(5, &last)]);
    let graph = message(1, &node(&merged));
    assert_eq!(observe(&model(&graph)).unwrap().tensors, 1);
    let reverse = cat(&[message(5, &last), message(5, &first)]);
    assert!(matches!(
        observe(&model(&message(1, &node(&reverse)))),
        Err(Pending::ExternalTensor { .. })
    ));
}
#[test]
fn p2_observe1_singular_graphs_merge_but_repeated_tensors_never_do() {
    let bytes = cat(&[
        integer(1, 13),
        message(7, &tensor_graph(&tensor(0))),
        message(7, &tensor_graph(&tensor(0))),
    ]);
    assert_eq!(observe(&bytes).unwrap().tensors, 2);
    let attributes = cat(&[message(10, &tensor(1)), message(10, &tensor(0))]);
    assert!(matches!(
        observe(&model(&message(1, &node(&attributes)))),
        Err(Pending::ExternalTensor { .. })
    ));
}
#[test]
fn p2_observe1_blob_bytes_are_not_mistaken_for_tensor_messages() {
    let fake = message(4, &tensor(1)); // Attribute.s is bytes, not TensorProto.
    let bytes = model(&message(1, &node(&fake)));
    assert_eq!(observe(&bytes).unwrap().tensors, 0);
    let mut raw = tensor(0);
    raw.extend(message(9, &tensor(1)));
    assert_eq!(observe(&model(&tensor_graph(&raw))).unwrap().tensors, 1);
}
#[test]
fn p2_observe1_unknown_format_schema_ir_and_location_never_claim_complete() {
    assert!(matches!(
        observe(&[0, 0, 0, 0, b'O', b'R', b'T', b'M']),
        Err(Pending::OrtFormat)
    ));
    assert!(matches!(
        observe(&cat(&[model(&[]), message(99, &[])])),
        Err(Pending::UnknownField { field: 99, .. })
    ));
    assert!(matches!(
        observe(&cat(&[model(&[]), integer(1, 14)])),
        Err(Pending::UnsupportedIr(14))
    ));
    assert!(matches!(
        observe(&model(&tensor_graph(&tensor(2)))),
        Err(Pending::UnknownLocation { value: 2, .. })
    ));
    assert!(matches!(
        observe(&integer(1, 13)),
        Err(Pending::MissingGraph)
    ));
    assert!(matches!(observe(&message(7, &[])), Err(Pending::MissingIr)));
}
#[test]
fn p2_observe1_wire_offsets_groups_varints_and_depth_are_bounded() {
    assert!(matches!(observe(&[0]), Err(Pending::Varint)));
    assert!(matches!(observe(&[10, 100, 1]), Err(Pending::WireOffset)));
    assert!(matches!(observe(&[11]), Err(Pending::WireType(3))));
    assert!(matches!(observe(&[128; 10]), Err(Pending::Varint)));
    let mut graph = tensor_graph(&tensor(0));
    for _ in 0..50 {
        graph = message(1, &node(&message(6, &graph)));
    }
    assert!(matches!(observe(&model(&graph)), Err(Pending::Depth)));
}
#[test]
fn p2_observe1_field_budget_is_a_pending_observation_not_completeness() {
    let mut bytes = model(&[]);
    for _ in 0..MAX_FIELDS {
        bytes.extend(message(2, &[]));
    }
    assert!(matches!(observe(&bytes), Err(Pending::FieldBudget)));
}
