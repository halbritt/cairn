//! The two performed Source stages. Values cannot enter FirstNormalizedRow by assertion.
use crate::{Error, FullOccurrenceMatrix, Layout, WIDTH};

/// Actual raw batch-size-one CLS result, before FastEmbed normalization.
pub struct RawClsRow([f32; WIDTH]);
impl RawClsRow {
    pub fn from_values(values: Vec<f32>) -> Result<Self, Error> {
        let values: [f32; WIDTH] = values
            .try_into()
            .map_err(|_| Error::Shape("raw CLS row must contain 384 float32 values".into()))?;
        Ok(Self(values))
    }
    /// Performs squares, NumPy pairwise reduction, sqrt, epsilon maximum and division.
    pub fn normalize(self) -> FirstNormalizedRow {
        let mut values = self.0;
        let mut norm = 0.0;
        let mut denominator = 0.0;
        // The row is exactly WIDTH floats, and both output scalars are distinct.
        // The exclusive row and stack outputs remain live; C retains no pointers.
        unsafe { cairn_norm_first(values.as_mut_ptr(), &mut norm, &mut denominator) };
        FirstNormalizedRow {
            values,
            norm,
            denominator,
        }
    }
}

/// Cache this immutable FIRST-pass value, never the second-pass matrix.
#[derive(Clone, Debug)]
pub struct FirstNormalizedRow {
    values: [f32; WIDTH],
    norm: f32,
    denominator: f32,
}
impl FirstNormalizedRow {
    pub fn values(&self) -> &[f32; WIDTH] {
        &self.values
    }
    pub fn norm(&self) -> f32 {
        self.norm
    }
    pub fn denominator(&self) -> f32 {
        self.denominator
    }
}

/// Distinct owned storage: query first, then every supplied passage occurrence.
/// Callers retain immutable first-pass cache rows; duplicate references are copied.
pub struct FirstPassOccurrences {
    layout: Layout,
    values: Vec<f32>,
}
impl FirstPassOccurrences {
    /// Rank owns prefix/model/cache/snapshot admission. This boundary copies
    /// its first-pass payload bits, preserving them exactly; it does NOT claim
    /// that cached/restored bytes prove prior inference or normalization.
    /// In particular it creates no FirstNormalizedRow or fabricated norm data.
    pub fn from_rank_first_pass_bits(
        full_rows: usize,
        columns: usize,
        bits: Vec<u32>,
    ) -> Result<Self, Error> {
        let layout = Layout::new(full_rows)?;
        if columns != WIDTH || bits.len() != layout.elements {
            return Err(Error::Shape(
                "rank first-pass storage/shape mismatch".into(),
            ));
        }
        let values = bits.into_iter().map(f32::from_bits).collect();
        Ok(Self { layout, values })
    }
    pub fn reconstruct(
        query: &FirstNormalizedRow,
        passages: &[&FirstNormalizedRow],
    ) -> Result<Self, Error> {
        let full_rows = passages
            .len()
            .checked_add(1)
            .ok_or_else(|| Error::Shape("occurrence count overflow".into()))?;
        let layout = Layout::new(full_rows)?;
        let mut values = Vec::with_capacity(layout.elements);
        values.extend_from_slice(query.values());
        for row in passages {
            values.extend_from_slice(row.values());
        }
        Ok(Self { layout, values })
    }
    pub fn values(&self) -> &[f32] {
        &self.values
    }
    /// Computes an independent norm for EVERY reconstructed row, with NO epsilon.
    pub fn norms_keepdims(self) -> SecondPassNorms {
        let mut norms = vec![0.0; self.layout.full_rows];
        // Layout bounds rows*WIDTH and pointer offsets; source and norm output
        // allocations are distinct, exactly sized and live for this call.
        unsafe {
            cairn_norm_rows(
                self.values.as_ptr(),
                self.layout.full_rows,
                norms.as_mut_ptr(),
            )
        };
        SecondPassNorms {
            matrix: self,
            norms,
        }
    }
}

/// Norms own their exact input matrix; they cannot be reused with another payload.
pub struct SecondPassNorms {
    matrix: FirstPassOccurrences,
    norms: Vec<f32>,
}
impl SecondPassNorms {
    pub fn norms(&self) -> &[f32] {
        &self.norms
    }
    pub fn values_before_division(&self) -> &[f32] {
        &self.matrix.values
    }
    pub fn any_zero_norm(&self) -> bool {
        self.norms.iter().any(|&norm| norm == 0.0)
    }
    /// Source checks all norms BEFORE mutating any row. Failure returns the owner.
    pub fn divide_in_place(mut self) -> Result<FullOccurrenceMatrix, ZeroNorm> {
        if self.any_zero_norm() {
            return Err(ZeroNorm { pending: self });
        }
        // Exclusive owned matrix and disjoint matching norms are exactly sized.
        // C changes only the matrix in this synchronous call, retaining no pointer.
        unsafe {
            cairn_norm_divide_rows(
                self.matrix.values.as_mut_ptr(),
                self.matrix.layout.full_rows,
                self.norms.as_ptr(),
            )
        };
        // Layout and storage were checked at reconstruction and are unchanged.
        Ok(
            FullOccurrenceMatrix::from_normalized(self.matrix.layout.full_rows, self.matrix.values)
                .expect("owned normalization layout is unchanged"),
        )
    }
}
/// The exact existing Source zero-norm decision; no finite/NaN/overflow policy is added.
pub struct ZeroNorm {
    pending: SecondPassNorms,
}
impl ZeroNorm {
    pub fn pending(&self) -> &SecondPassNorms {
        &self.pending
    }
    pub fn into_pending(self) -> SecondPassNorms {
        self.pending
    }
}
impl std::fmt::Debug for ZeroNorm {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ZeroNorm")
            .field("norms", &self.pending.norms)
            .finish()
    }
}
impl std::fmt::Display for ZeroNorm {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("empty model embedding")
    }
}
impl std::error::Error for ZeroNorm {}

extern "C" {
    fn cairn_norm_first(row: *mut f32, norm: *mut f32, denominator: *mut f32);
    fn cairn_norm_rows(values: *const f32, rows: usize, norms: *mut f32);
    fn cairn_norm_divide_rows(values: *mut f32, rows: usize, norms: *const f32);
}

#[cfg(test)]
mod tests;
