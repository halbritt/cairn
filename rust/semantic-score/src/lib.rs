//! Single-note post-matrix scoring for Cairn's semantic/4 rank contract.

/// Reduces one note's supplied chunk similarities to a bounded integer score.
///
/// Promotes each value to `f64`, takes the ordered maximum starting at `-1.0`,
/// clamps the final maximum, scales in `f64`, then rounds ties to even.
/// An empty slice returns `-1_000_000`; worker admission is a separate concern.
pub fn rank_note_score(similarities: &[f32]) -> i32 {
    let mut maximum = -1.0_f64;
    for &similarity in similarities {
        let similarity = f64::from(similarity);
        // Source keeps the previous maximum when the comparison is unordered.
        if similarity > maximum {
            maximum = similarity;
        }
    }
    let clamped = maximum.clamp(-1.0, 1.0);
    (clamped * 1_000_000.0).round_ties_even() as i32
}

#[cfg(test)]
mod tests {
    use super::rank_note_score;

    #[test]
    fn supplied_chunks_reduce_to_bounded_note_score() {
        let cases: &[(&[f32], i32)] = &[
            (&[], -1_000_000),
            (&[-2.0, -1.5], -1_000_000),
            (&[-1.0], -1_000_000),
            (&[1.0], 1_000_000),
            (&[1.5, 0.5], 1_000_000),
            (&[f32::MAX], 1_000_000),
            (&[-f32::MAX], -1_000_000),
            (&[0.25], 250_000),
            (&[-0.75, -0.25, -0.5], -250_000),
            (&[-0.75, 0.875, -0.5], 875_000),
            (&[0.25, 0.75, 0.75, -0.5], 750_000),
            (&[0.5, -0.5], 500_000),
            (&[-0.5, 0.5], 500_000),
        ];
        for &(similarities, expected) in cases {
            assert_eq!(rank_note_score(similarities), expected, "{similarities:?}");
        }
    }

    #[test]
    fn binary_ties_and_adjacent_f32_values_round_to_even() {
        // Independent literal outcomes for +/-1/128, +/-3/128, +/-5/128
        // and the immediately adjacent f32 bit patterns.
        for (bits, expected) in [
            (0x3bff_ffff, 7_812),
            (0x3c00_0000, 7_812),
            (0x3c00_0001, 7_813),
            (0xbbff_ffff, -7_812),
            (0xbc00_0000, -7_812),
            (0xbc00_0001, -7_813),
            (0x3cbf_ffff, 23_437),
            (0x3cc0_0000, 23_438),
            (0x3cc0_0001, 23_438),
            (0xbcbf_ffff, -23_437),
            (0xbcc0_0000, -23_438),
            (0xbcc0_0001, -23_438),
            (0x3d1f_ffff, 39_062),
            (0x3d20_0000, 39_062),
            (0x3d20_0001, 39_063),
            (0xbd1f_ffff, -39_062),
            (0xbd20_0000, -39_062),
            (0xbd20_0001, -39_063),
        ] {
            assert_eq!(
                rank_note_score(&[f32::from_bits(bits)]),
                expected,
                "input bits {bits:#010x}"
            );
        }
    }

    #[test]
    fn promotion_precedes_scaling_at_f32_rounding_boundary() {
        // Synthetic fixture: the positive exact f64 product is
        // 258907046875/2097152, whereas f32 scaling produces 123456.5.
        for (bits, expected) in [(0x3dfc_d6c3, 123_457), (0xbdfc_d6c3, -123_457)] {
            assert_eq!(
                rank_note_score(&[f32::from_bits(bits)]),
                expected,
                "input bits {bits:#010x}"
            );
        }
    }

    #[test]
    fn signed_zero_and_nonfinite_inputs_keep_comparison_semantics() {
        let cases: &[(&[f32], i32)] = &[
            (&[-0.0, 0.0], 0),
            (&[0.0, -0.0], 0),
            (&[f32::NAN], -1_000_000),
            (&[f32::NAN, f32::NAN], -1_000_000),
            (&[f32::NAN, 0.25], 250_000),
            (&[0.25, f32::NAN, 0.5], 500_000),
            (&[-0.25, f32::NAN, -0.5], -250_000),
            (&[f32::NEG_INFINITY], -1_000_000),
            (&[f32::NAN, f32::NEG_INFINITY], -1_000_000),
            (&[f32::NEG_INFINITY, 0.25, f32::NAN], 250_000),
            (&[f32::INFINITY, f32::NAN], 1_000_000),
            (&[f32::NAN, f32::NEG_INFINITY, f32::INFINITY], 1_000_000),
        ];
        for &(similarities, expected) in cases {
            assert_eq!(rank_note_score(similarities), expected, "{similarities:?}");
        }
    }
}
