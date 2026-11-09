use super::*;

fn bytes(text: &[u8]) -> ffi::Bytes {
    ffi::Bytes {
        data: text.as_ptr(),
        length: text.len(),
    }
}
fn node(name: &[u8], domain: &[u8], operator_type: &[u8]) -> ffi::AssignedNode {
    ffi::AssignedNode {
        name: bytes(name),
        domain: bytes(domain),
        operator_type: bytes(operator_type),
    }
}
fn group(provider: &[u8], nodes: &[ffi::AssignedNode]) -> ffi::AssignedSubgraph {
    ffi::AssignedSubgraph {
        provider: bytes(provider),
        node_count: nodes.len(),
        nodes: nodes.as_ptr(),
    }
}
fn snapshot(groups: &[ffi::AssignedSubgraph]) -> ffi::AssignmentView {
    ffi::AssignmentView {
        observed: 1,
        reserved: 0,
        count: groups.len(),
        subgraphs: groups.as_ptr(),
    }
}

#[test]
fn p2_dispatch1_missing_and_malformed_snapshots_are_not_observed_empty() {
    assert!(decode_graph_assignment(&ffi::AssignmentView::default()).is_err());
    let mut raw = ffi::AssignmentView {
        observed: 1,
        ..Default::default()
    };
    assert!(decode_graph_assignment(&raw)
        .unwrap()
        .subgraphs()
        .is_empty());
    raw.observed = 2;
    assert!(decode_graph_assignment(&raw).is_err());
    raw.observed = 1;
    raw.reserved = 1;
    assert!(decode_graph_assignment(&raw).is_err());
    raw.reserved = 0;
    raw.count = 1;
    assert!(decode_graph_assignment(&raw).is_err());
    raw.subgraphs = NonNull::<ffi::AssignedSubgraph>::dangling().as_ptr();
    raw.count = usize::MAX;
    assert!(decode_graph_assignment(&raw).is_err());
}

#[test]
fn p2_dispatch1_groups_order_duplicates_and_exact_bytes_survive() {
    let nodes = [
        node(b"z", b"", b"Add"),
        node(b"z", b"", b"Add"),
        node(&[255], b"ai.onnx", b"Mul"),
    ];
    let groups = [
        group(&[254], &nodes),
        group(b"CPU", &[]),
        group(&[254], &nodes),
    ];
    let observed = decode_graph_assignment(&snapshot(&groups)).unwrap();
    assert_eq!(observed.subgraphs().len(), 3);
    assert_eq!(observed.subgraphs()[0], observed.subgraphs()[2]);
    assert_eq!(observed.subgraphs()[0].provider(), &[254]);
    assert!(observed.subgraphs()[1].nodes().is_empty());
    let rows = observed.subgraphs()[0].nodes();
    assert_eq!(rows.len(), 3);
    assert_eq!(rows[0], rows[1]);
    assert_eq!(rows[0].name(), b"z");
    assert_eq!(rows[0].domain(), b"");
    assert_eq!(rows[0].operator_type(), b"Add");
    assert_eq!(rows[2].name(), &[255]);
    assert_eq!(rows[2].domain(), b"ai.onnx");
    assert_eq!(rows[2].operator_type(), b"Mul");
}

#[test]
fn p2_dispatch1_every_string_is_owned_after_source_mutation_and_drop() {
    let observed = {
        let mut provider = b"CPU".to_vec();
        let mut name = b"node".to_vec();
        let mut domain = b"domain".to_vec();
        let mut operator = b"Add".to_vec();
        let nodes = [node(&name, &domain, &operator)];
        let groups = [group(&provider, &nodes)];
        let observed = decode_graph_assignment(&snapshot(&groups)).unwrap();
        provider.fill(b'x');
        name.fill(b'x');
        domain.fill(b'x');
        operator.fill(b'x');
        observed
    };
    let group = &observed.subgraphs()[0];
    assert_eq!(group.provider(), b"CPU");
    assert_eq!(group.nodes()[0].name(), b"node");
    assert_eq!(group.nodes()[0].domain(), b"domain");
    assert_eq!(group.nodes()[0].operator_type(), b"Add");
}

#[test]
fn p2_dispatch1_node_array_bounds_reject_before_pointer_dereference() {
    let mut groups = [ffi::AssignedSubgraph {
        provider: bytes(b"CPU"),
        node_count: 0,
        nodes: std::ptr::null(),
    }];
    assert!(decode_graph_assignment(&snapshot(&groups))
        .unwrap()
        .subgraphs()[0]
        .nodes()
        .is_empty());
    groups[0].node_count = 1;
    assert!(decode_graph_assignment(&snapshot(&groups)).is_err());
    groups[0].nodes = NonNull::<ffi::AssignedNode>::dangling().as_ptr();
    groups[0].node_count = usize::MAX;
    assert!(decode_graph_assignment(&snapshot(&groups)).is_err());
}

#[test]
fn p2_dispatch1_null_required_text_refuses_but_empty_text_is_retained() {
    let empty = node(b"", b"", b"");
    let nodes = [empty];
    let groups = [group(b"", &nodes)];
    let observed = decode_graph_assignment(&snapshot(&groups)).unwrap();
    assert!(observed.subgraphs()[0].provider().is_empty());
    assert!(observed.subgraphs()[0].nodes()[0].domain().is_empty());
    let missing = ffi::Bytes::default();
    for changed in [
        ffi::AssignedNode {
            name: missing,
            ..empty
        },
        ffi::AssignedNode {
            domain: missing,
            ..empty
        },
        ffi::AssignedNode {
            operator_type: missing,
            ..empty
        },
    ] {
        assert!(decode_graph_assignment(&snapshot(&[group(b"CPU", &[changed])])).is_err());
    }
    let mut invalid_provider = groups;
    invalid_provider[0].provider = missing;
    assert!(decode_graph_assignment(&snapshot(&invalid_provider)).is_err());
    let mut oversized = groups;
    oversized[0].provider = ffi::Bytes {
        data: NonNull::<u8>::dangling().as_ptr(),
        length: usize::MAX,
    };
    assert!(decode_graph_assignment(&snapshot(&oversized)).is_err());
}

#[test]
fn p2_dispatch1_reordering_or_regrouping_nodes_changes_the_snapshot() {
    let nodes = [node(b"z", b"", b"Add"), node(b"a", b"", b"Mul")];
    let baseline = decode_graph_assignment(&snapshot(&[group(b"CPU", &nodes)])).unwrap();
    let reversed = [nodes[1], nodes[0]];
    assert_ne!(
        baseline,
        decode_graph_assignment(&snapshot(&[group(b"CPU", &reversed)])).unwrap()
    );
    let regrouped = [group(b"CPU", &nodes[..1]), group(b"CPU", &nodes[1..])];
    assert_ne!(
        baseline,
        decode_graph_assignment(&snapshot(&regrouped)).unwrap()
    );
}
