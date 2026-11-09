use super::*;
use crate::{ElementType, NodeMetadata};
fn bytes(v: &[u8]) -> ffi::Bytes {
    ffi::Bytes {
        data: v.as_ptr(),
        length: v.len(),
    }
}
fn entry(k: &[u8], v: &[u8]) -> ffi::ConfigEntry {
    ffi::ConfigEntry {
        key: bytes(k),
        value: bytes(v),
    }
}
fn config(rows: &[ffi::ConfigEntry]) -> ffi::ConfigView {
    ffi::ConfigView {
        observed: 1,
        reserved: 0,
        count: rows.len(),
        entries: rows.as_ptr(),
    }
}
fn node(name: &str) -> NodeMetadata {
    NodeMetadata {
        name: name.into(),
        element_type: ElementType::Float32,
        dimensions: vec![1, 384],
    }
}
fn io(index: usize, name: &[u8]) -> ffi::IoObservation {
    ffi::IoObservation {
        ordinal: index,
        node_name: bytes(name),
        ..Default::default()
    }
}

#[test]
fn p2_session1_observed_empty_config_is_not_missing_or_malformed() {
    assert!(decode_config(&ffi::ConfigView::default()).is_err());
    assert!(decode_config(&config(&[])).unwrap().is_empty());
    let mut raw = config(&[]);
    raw.observed = 2;
    assert!(decode_config(&raw).is_err());
    raw.observed = 1;
    raw.count = 1;
    raw.entries = std::ptr::null();
    assert!(decode_config(&raw).is_err());
    raw.entries = NonNull::<ffi::ConfigEntry>::dangling().as_ptr();
    raw.count = usize::MAX;
    assert!(decode_config(&raw).is_err());
}
#[test]
fn p2_session1_config_canonicalization_keeps_duplicates_and_non_utf8_distinct() {
    let rows = [
        entry(b"z", b"later"),
        entry(&[255], b"a"),
        entry(&[254], b"b"),
        entry(b"z", b"earlier"),
        entry(b"z", b"earlier"),
    ];
    let first = decode_config(&config(&rows)).unwrap();
    let mut permuted = rows;
    permuted.reverse();
    assert_eq!(first, decode_config(&config(&permuted)).unwrap());
    assert_eq!(
        first
            .iter()
            .map(|e| (e.key(), e.value()))
            .collect::<Vec<_>>(),
        vec![
            (&b"z"[..], &b"earlier"[..]),
            (&b"z"[..], &b"earlier"[..]),
            (&b"z"[..], &b"later"[..]),
            (&[254][..], &b"b"[..]),
            (&[255][..], &b"a"[..])
        ]
    );
}
#[test]
fn p2_session1_config_copies_survive_fixture_mutation_and_drop() {
    let observed = {
        let mut key = b"key".to_vec();
        let mut value = vec![255, 0x61];
        let rows = [entry(&key, &value)];
        let owned = decode_config(&config(&rows)).unwrap();
        key.fill(b'x');
        value.fill(b'x');
        assert_eq!(owned[0].key(), b"key");
        owned
    };
    assert_eq!(observed[0].value(), &[255, 0x61]);
}
#[test]
fn p2_session1_io_count_ordinal_and_name_bindings_reject_reassociation() {
    let nodes = [node("zulu"), node("alpha")];
    let rows = [io(0, b"zulu"), io(1, b"alpha")];
    let observed = decode_io(&nodes, &rows).unwrap();
    assert_eq!(observed[0].node_name(), "zulu");
    assert_eq!(observed[1].ordinal(), 1);
    assert!(decode_io(&nodes, &rows[..1]).is_err());
    assert!(decode_io(&nodes, &[rows[1], rows[0]]).is_err());
    let mut wrong = rows;
    wrong[0].node_name = bytes(b"alpha");
    assert!(decode_io(&nodes, &wrong).is_err());
    let same = [node("same"), node("same")];
    let duplicate = [io(0, b"same"), io(1, b"same")];
    assert_eq!(decode_io(&same, &duplicate).unwrap().len(), 2);
    assert!(decode_io(&same, &[duplicate[1], duplicate[0]]).is_err());
}
#[test]
fn p2_session1_memory_dimensions_and_unknown_native_codes_are_retained() {
    let raw = ffi::MemoryObservation {
        present: 1,
        name: bytes(b"Cpu"),
        device_type: 0,
        device_id: 7,
        allocator_type: 1,
        memory_type: -1,
        device_memory_type: 5,
        vendor_id: 0x1234,
        ..Default::default()
    };
    let m = decode_memory(&raw).unwrap().unwrap();
    assert_eq!(m.device_type().code(), 0);
    assert_eq!(m.device_id(), 7);
    assert_eq!(m.allocator_type().code(), 1);
    assert_eq!(m.memory_type().code(), -1);
    assert_eq!(m.device_memory_type().code(), 5);
    assert_eq!(m.vendor_id(), 0x1234);
    let unknown = ffi::MemoryObservation {
        device_type: 99,
        allocator_type: 98,
        memory_type: -99,
        device_memory_type: 97,
        ..raw
    };
    let m = decode_memory(&unknown).unwrap().unwrap();
    assert_eq!(m.device_type().code(), 99);
    assert_eq!(m.allocator_type().code(), 98);
    assert_eq!(m.memory_type().code(), -99);
    assert_eq!(m.device_memory_type().code(), 97);
}
#[test]
fn p2_session1_null_ep_device_is_distinct_from_observed_or_empty_identity() {
    assert_eq!(decode_ep(&ffi::EpObservation::default()).unwrap(), None);
    let mut row = io(0, b"node");
    row.memory = ffi::MemoryObservation {
        present: 1,
        name: bytes(b"Cpu"),
        allocator_type: 1,
        ..Default::default()
    };
    let observed = decode_io(&[node("node")], &[row]).unwrap();
    assert!(observed[0].ep_device().is_none());
    assert_eq!(observed[0].memory().unwrap().allocator_type().code(), 1);
    let absent = decode_io(&[node("node")], &[io(0, b"node")]).unwrap();
    assert!(absent[0].memory().is_none());
    let unknown = ffi::EpObservation {
        present: 1,
        ..Default::default()
    };
    let ep = decode_ep(&unknown).unwrap().unwrap();
    assert_eq!(ep.name(), None);
    assert_eq!(ep.vendor(), None);
    let named = ffi::EpObservation {
        present: 1,
        name: bytes(b"CPUExecutionProvider"),
        vendor: bytes(b""),
        ..Default::default()
    };
    let ep = decode_ep(&named).unwrap().unwrap();
    assert_eq!(ep.name(), Some(&b"CPUExecutionProvider"[..]));
    assert_eq!(ep.vendor(), Some(&b""[..]));
    assert_ne!(decode_ep(&unknown).unwrap(), decode_ep(&named).unwrap());
}
#[test]
fn p2_session1_invalid_presence_or_byte_views_refuse_before_dereference() {
    let missing = ffi::MemoryObservation {
        name: bytes(b"unexpected"),
        ..Default::default()
    };
    assert!(decode_memory(&missing).is_err());
    let missing = ffi::EpObservation {
        name: bytes(b"unexpected"),
        ..Default::default()
    };
    assert!(decode_ep(&missing).is_err());
    assert!(decode_ep(&ffi::EpObservation {
        present: 2,
        ..Default::default()
    })
    .is_err());
    assert!(decode_memory(&ffi::MemoryObservation {
        reserved: 1,
        ..Default::default()
    })
    .is_err());
    assert!(copy_optional_bytes(ffi::Bytes {
        data: std::ptr::null(),
        length: 1
    })
    .is_err());
    assert!(copy_required_bytes(ffi::Bytes {
        data: std::ptr::NonNull::<u8>::dangling().as_ptr(),
        length: usize::MAX
    })
    .is_err());
}
#[test]
fn p2_session1_io_evidence_owns_bytes_after_borrowed_source_changes() {
    let owned = {
        let nodes = [node("first")];
        let mut memory = b"Cpu".to_vec();
        let mut provider = b"provider".to_vec();
        let vendor = [255u8];
        let mut row = io(0, b"first");
        row.memory = ffi::MemoryObservation {
            present: 1,
            name: bytes(&memory),
            ..Default::default()
        };
        row.ep = ffi::EpObservation {
            present: 1,
            name: bytes(&provider),
            vendor: bytes(&vendor),
            ..Default::default()
        };
        let owned = decode_io(&nodes, &[row]).unwrap();
        memory.fill(b'x');
        provider.fill(b'x');
        owned
    };
    assert_eq!(owned[0].memory().unwrap().name(), b"Cpu");
    assert_eq!(owned[0].ep_device().unwrap().name(), Some(&b"provider"[..]));
    assert_eq!(owned[0].ep_device().unwrap().vendor(), Some(&[255][..]));
}
