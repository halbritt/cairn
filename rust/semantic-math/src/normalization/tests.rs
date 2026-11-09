//! Synthetic component cases, not selected NumPy/ORT reference golden vectors.
//! Later bounded execution must not be reported as actual-runtime parity.
//! Arithmetic cases assume round-to-nearest/even with gradual underflow.
use super::*;

fn bits(values: &[f32]) -> Vec<u32> {
    values.iter().map(|v| v.to_bits()).collect()
}
fn one_hot(index: usize, value: f32) -> FirstNormalizedRow {
    let mut values = vec![0.0; WIDTH];
    values[index] = value;
    RawClsRow::from_values(values).unwrap().normalize()
}
fn tiny() -> f32 {
    // Exactly 2^-50: its squared value is normal binary32; below the epsilon floor.
    f32::from_bits((127 - 50) << 23)
}

#[test]
fn raw_cls_shape_errors_do_not_treat_two_rows_or_padding_as_one_row() {
    for length in [0, 1, WIDTH - 1, WIDTH + 1, WIDTH * 2] {
        let error = RawClsRow::from_values(vec![0.0; length]).err().unwrap();
        assert!(matches!(error, Error::Shape(_)));
        assert!(error.to_string().contains("384 float32 values"));
    }
    assert!(RawClsRow::from_values(vec![0.0; WIDTH]).is_ok());
}

#[test]
fn first_stage_performs_real_norm_and_all_component_divisions() {
    // Analytic synthetic 3-4-5 triangle. No computed implementation golden bits.
    let mut values = vec![0.0; WIDTH];
    values[0] = 3.0;
    values[WIDTH - 1] = -4.0;
    let row = RawClsRow::from_values(values).unwrap().normalize();
    assert_eq!(row.norm().to_bits(), 5.0f32.to_bits());
    assert_eq!(row.denominator().to_bits(), 5.0f32.to_bits());
    assert_eq!(row.values()[0].to_bits(), 0.6f32.to_bits());
    assert_eq!(row.values()[WIDTH - 1].to_bits(), (-0.8f32).to_bits());
    assert!(row.values()[1..WIDTH - 1].iter().all(|v| v.to_bits() == 0));
}

#[test]
fn first_epsilon_floor_and_second_unfloored_norm_are_distinct_performed_stages() {
    let row = one_hot(17, tiny());
    assert_eq!(row.norm().to_bits(), tiny().to_bits());
    assert_eq!(row.denominator().to_bits(), 1.0e-12f32.to_bits());
    assert!(row.values()[17] > 0.0 && row.values()[17] < 1.0);
    let cache_before = bits(row.values());
    let pending = FirstPassOccurrences::reconstruct(&row, &[&row])
        .unwrap()
        .norms_keepdims();
    assert_eq!(pending.norms().len(), 2);
    assert_eq!(pending.norms()[0].to_bits(), row.values()[17].to_bits());
    assert_eq!(pending.norms()[1].to_bits(), row.values()[17].to_bits());
    assert!(!pending.any_zero_norm());
    let matrix = pending.divide_in_place().unwrap();
    assert_eq!(matrix.query()[17].to_bits(), 1.0f32.to_bits());
    assert_eq!(matrix.passages()[17].to_bits(), 1.0f32.to_bits());
    assert_ne!(bits(matrix.query()), cache_before);
    assert_eq!(bits(row.values()), cache_before);
}

#[test]
fn second_stage_zero_decision_precedes_every_mutation_and_keeps_signed_zero() {
    let query = one_hot(0, tiny());
    let zero = RawClsRow::from_values(vec![-0.0; WIDTH])
        .unwrap()
        .normalize();
    assert_eq!(zero.norm().to_bits(), 0.0f32.to_bits());
    assert_eq!(zero.denominator().to_bits(), 1.0e-12f32.to_bits());
    assert!(zero.values().iter().all(|v| v.to_bits() == 0x8000_0000));
    let occurrences = FirstPassOccurrences::reconstruct(&query, &[&query, &zero, &query]).unwrap();
    let before = bits(occurrences.values());
    let pending = occurrences.norms_keepdims();
    assert_eq!(bits(pending.values_before_division()), before);
    assert!(pending.any_zero_norm());
    assert_eq!(pending.norms()[2].to_bits(), 0);
    let error = pending.divide_in_place().err().unwrap();
    assert_eq!(error.to_string(), "empty model embedding");
    assert_eq!(bits(error.pending().values_before_division()), before);
    assert!(error.into_pending().any_zero_norm());
}

#[test]
fn full_occurrences_preserve_warm_cold_duplicates_order_and_immutable_cache() {
    let query = one_hot(0, tiny());
    let cold_a = one_hot(1, tiny());
    let cold_b = one_hot(2, -tiny());
    let warm_a = cold_a.clone();
    let warm_b = cold_b.clone();
    let cache_a = bits(warm_a.values());
    let cache_b = bits(warm_b.values());
    let cold =
        FirstPassOccurrences::reconstruct(&query, &[&cold_a, &cold_b, &cold_a, &cold_b, &cold_a])
            .unwrap();
    let warm =
        FirstPassOccurrences::reconstruct(&query, &[&warm_a, &warm_b, &warm_a, &warm_b, &warm_a])
            .unwrap();
    assert_eq!(bits(cold.values()), bits(warm.values()));
    assert_eq!(warm.values().len(), 6 * WIDTH);
    let pending = warm.norms_keepdims();
    assert_eq!(pending.norms().len(), 6);
    assert!(pending.norms().iter().all(|&n| n == pending.norms()[0]));
    let matrix = pending.divide_in_place().unwrap();
    assert_eq!(matrix.layout().full_rows, 6);
    assert_eq!(matrix.layout().operation, crate::Operation::Gemv384);
    assert_eq!(matrix.query()[0].to_bits(), 1.0f32.to_bits());
    for (row, (index, sign)) in matrix.passages().chunks_exact(WIDTH).zip([
        (1, 1.0f32),
        (2, -1.0),
        (1, 1.0),
        (2, -1.0),
        (1, 1.0),
    ]) {
        assert_eq!(row[index].to_bits(), sign.to_bits());
        assert_eq!(row.iter().filter(|&&x| x != 0.0).count(), 1);
    }
    assert_eq!(bits(warm_a.values()), cache_a);
    assert_eq!(bits(warm_b.values()), cache_b);
    let cold_matrix = cold.norms_keepdims().divide_in_place().unwrap();
    assert_eq!(bits(cold_matrix.query()), bits(matrix.query()));
    assert_eq!(bits(cold_matrix.passages()), bits(matrix.passages()));
}

#[test]
fn query_only_second_pass_preserves_empty_matvec_dispatch() {
    let query = one_hot(383, -2.0);
    let pending = FirstPassOccurrences::reconstruct(&query, &[])
        .unwrap()
        .norms_keepdims();
    assert_eq!(pending.norms().len(), 1);
    assert!(!pending.any_zero_norm());
    let matrix = pending.divide_in_place().unwrap();
    assert_eq!(matrix.layout().operation, crate::Operation::Empty);
    assert!(matrix.passages().is_empty());
    assert_eq!(matrix.query()[383].to_bits(), (-1.0f32).to_bits());
}

#[test]
fn nonfinite_and_overflow_inputs_are_not_sanitized_or_numerically_refused() {
    // The unchanged rank owner checks finite model.embed output. This component
    // itself follows the selected arithmetic without a new numeric policy.
    for value in [f32::INFINITY, f32::NEG_INFINITY, f32::MAX] {
        let row = one_hot(0, value);
        assert_eq!(row.norm(), f32::INFINITY);
        assert_eq!(row.denominator(), f32::INFINITY);
        if value.is_finite() {
            assert_eq!(row.values()[0], 0.0);
        } else {
            assert!(row.values()[0].is_nan());
        }
    }
    let row = one_hot(0, f32::from_bits(0x7fc0_1234));
    assert!(row.norm().is_nan());
    assert!(row.denominator().is_nan());
    assert!(row.values().iter().all(|v| v.is_nan()));
    let pending = FirstPassOccurrences::reconstruct(&row, &[&row])
        .unwrap()
        .norms_keepdims();
    assert!(pending.norms().iter().all(|v| v.is_nan()));
    assert!(!pending.any_zero_norm());
    assert!(pending.divide_in_place().is_ok());
}

#[test]
fn subnormal_square_underflow_flows_to_the_existing_second_stage_zero_guard() {
    // Smallest positive binary32 input: its square rounds to +0 in default
    // round-to-nearest. The original value is still divided by epsilon.
    let row = one_hot(0, f32::from_bits(1));
    assert_eq!(row.norm().to_bits(), 0);
    assert_eq!(row.denominator().to_bits(), 1.0e-12f32.to_bits());
    assert!(row.values()[0] > 0.0);
    let pending = FirstPassOccurrences::reconstruct(&row, &[])
        .unwrap()
        .norms_keepdims();
    assert!(pending.any_zero_norm());
    assert!(pending.divide_in_place().is_err());
}

#[test]
fn source_pairwise_384_grouping_differs_from_sequential_and_f64_sum() {
    // Analytic synthetic case from NumPy's selected grouping, not a captured
    // NumPy golden. Squares are [2^24, 1 x383]. Four 96-element blocks have
    // eight lanes each. The large lane loses exactly eleven additions of 1;
    // the reference grouping therefore sums 2^24+372, not sequential 2^24
    // or f64-accumulated-and-rounded 2^24+384. Correctly rounded sqrt is
    // 4096 + 93*2^-11; the latter alternatives round to +0 and +96*2^-11.
    let mut raw = vec![1.0; WIDTH];
    raw[0] = 4096.0;
    let row = RawClsRow::from_values(raw).unwrap().normalize();
    let expected = 4096.0f32 + 93.0f32 / 2048.0f32;
    assert_eq!(row.norm().to_bits(), expected.to_bits());
    assert_ne!(row.norm().to_bits(), 4096.0f32.to_bits());
    assert_ne!(
        row.norm().to_bits(),
        (4096.0f32 + 96.0f32 / 2048.0f32).to_bits()
    );
}
