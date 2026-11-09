use crate::layout::ROW_BYTES;
use crate::{hex, payload_digest, FullOccurrenceMatrix, Layout, Operation, LIBRARIES, WIDTH};

#[test]
fn numpy_zero_one_and_many_passage_dispatch_is_not_per_row_dot() {
    for (rows, operation) in [
        (1, Operation::Empty),
        (2, Operation::Dot384),
        (3, Operation::Gemv384),
        (129, Operation::Gemv384),
    ] {
        let layout = Layout::new(rows).unwrap();
        assert_eq!(layout.operation, operation);
        assert_eq!(layout.passage_rows, rows - 1);
        assert_eq!(layout.blas_rows, (rows - 1) as i64);
        assert_eq!(layout.elements, rows * 384);
        assert_eq!(layout.bytes, rows * 1536);
    }
}
#[test]
fn query_row_is_required_and_full_storage_must_match_exactly() {
    assert!(Layout::new(0).is_err());
    assert!(FullOccurrenceMatrix::from_normalized(0, vec![]).is_err());
    for length in [0, 383, 385, 767, 769] {
        assert!(FullOccurrenceMatrix::from_normalized(2, vec![0.0; length]).is_err());
    }
    assert!(FullOccurrenceMatrix::from_normalized(2, vec![0.0; 768]).is_ok());
}
#[test]
fn full_occurrences_keep_duplicate_and_cache_rows_in_source_order() {
    // Synthetic already-normalized-stage data, not native result/parity evidence.
    let mut values = Vec::new();
    for marker in [1.0f32, 2.0, 3.0, 2.0, 4.0, 3.0] {
        values.extend(vec![marker; WIDTH]);
    }
    let matrix = FullOccurrenceMatrix::from_normalized(6, values).unwrap();
    assert!(matrix.query().iter().all(|&x| x == 1.0));
    assert_eq!(matrix.passages().len(), 5 * WIDTH);
    let markers: Vec<_> = matrix
        .passages()
        .chunks_exact(WIDTH)
        .map(|row| row[0])
        .collect();
    assert_eq!(markers, vec![2.0, 3.0, 2.0, 4.0, 3.0]);
    assert_eq!(matrix.layout().operation, Operation::Gemv384);
}
#[test]
fn numeric_payloads_are_neither_refused_nor_rewritten_by_layout() {
    let special = [0u32, 0x8000_0000, 0x7fc0_1234, 0x7f80_0000, 0xff80_0000, 1];
    let mut values = vec![1.0; 2 * WIDTH];
    for (index, bits) in special.into_iter().enumerate() {
        values[WIDTH + index] = f32::from_bits(bits);
    }
    let matrix = FullOccurrenceMatrix::from_normalized(2, values).unwrap();
    assert_eq!(
        matrix.passages()[..special.len()]
            .iter()
            .map(|x| x.to_bits())
            .collect::<Vec<_>>(),
        special.to_vec()
    );
}
#[test]
fn byte_overflow_pointer_limits_and_ilp64_sizing_are_checked_without_allocation() {
    assert!(Layout::new(usize::MAX).is_err());
    let maximum_rows = isize::MAX as usize / ROW_BYTES;
    let layout = Layout::new(maximum_rows).unwrap();
    assert!(layout.bytes <= isize::MAX as usize);
    assert_eq!(layout.blas_rows, i64::try_from(maximum_rows - 1).unwrap());
    assert!(Layout::new(maximum_rows + 1).is_err());
}
#[test]
fn contiguous_query_and_passage_ranges_do_not_cross_or_pad_rows() {
    let layout = Layout::new(4).unwrap();
    assert_eq!(layout.query(), 0..384);
    assert_eq!(layout.passages(), 384..1536);
    let values: Vec<_> = (0..1536).map(|i| i as f32).collect();
    let matrix = FullOccurrenceMatrix::from_normalized(4, values).unwrap();
    assert_eq!(matrix.query()[383], 383.0);
    assert_eq!(matrix.passages()[0], 384.0);
    assert_eq!(matrix.passages()[383], 767.0);
    assert_eq!(matrix.passages()[384], 768.0);
    assert_eq!(matrix.passages()[1151], 1535.0);
}
#[test]
fn real_pin_validation_refuses_wrong_length_and_same_length_wrong_digest() {
    let pin = LIBRARIES[2];
    assert!(payload_digest(&[], Some(pin)).is_err());
    assert!(payload_digest(&vec![0; pin.bytes], Some(pin)).is_err());
    assert_eq!(
        hex(&payload_digest(b"abc", None).unwrap()),
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    );
}
