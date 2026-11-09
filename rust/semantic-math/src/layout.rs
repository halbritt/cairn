use crate::Error;
use std::ops::Range;
pub const WIDTH: usize = 384;
pub const ROW_BYTES: usize = WIDTH * std::mem::size_of::<f32>();

#[repr(i32)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Operation {
    Empty = 0,
    Dot384 = 1,
    Gemv384 = 2,
}

/// Query row followed by every chunk occurrence, including duplicates/cache hits.
/// Values have already passed both required normalizations in the composing worker.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Layout {
    pub full_rows: usize,
    pub passage_rows: usize,
    pub elements: usize,
    pub bytes: usize,
    pub blas_rows: i64,
    pub operation: Operation,
}
impl Layout {
    pub fn new(full_rows: usize) -> Result<Self, Error> {
        let passage_rows = full_rows
            .checked_sub(1)
            .ok_or_else(|| Error::Shape("missing query row".into()))?;
        let elements = full_rows
            .checked_mul(WIDTH)
            .ok_or_else(|| Error::Shape("element count overflow".into()))?;
        let bytes = elements
            .checked_mul(std::mem::size_of::<f32>())
            .ok_or_else(|| Error::Shape("byte count overflow".into()))?;
        if bytes > isize::MAX as usize {
            return Err(Error::Shape("buffer exceeds pointer-offset bound".into()));
        }
        let blas_rows = i64::try_from(passage_rows)
            .map_err(|_| Error::Shape("ILP64 row count overflow".into()))?;
        if blas_rows > i64::MAX - 1 {
            return Err(Error::Shape("NumPy BLAS_MAXSIZE exceeded".into()));
        }
        // NumPy2.4.6 matmul.c.src:463,531,547; d_n=384,d_p=1.
        let operation = match passage_rows {
            0 => Operation::Empty,
            1 => Operation::Dot384,
            _ => Operation::Gemv384,
        };
        Ok(Self {
            full_rows,
            passage_rows,
            elements,
            bytes,
            blas_rows,
            operation,
        })
    }
    pub fn query(&self) -> Range<usize> {
        0..WIDTH
    }
    pub fn passages(&self) -> Range<usize> {
        WIDTH..self.elements
    }
}

pub struct FullOccurrenceMatrix {
    layout: Layout,
    values: Vec<f32>,
}
impl FullOccurrenceMatrix {
    /// No numeric finiteness/norm guard is added to the reference output policy.
    pub fn from_normalized(full_rows: usize, values: Vec<f32>) -> Result<Self, Error> {
        let layout = Layout::new(full_rows)?;
        if values.len() != layout.elements {
            return Err(Error::Shape(
                "matrix storage differs from full-occurrence shape".into(),
            ));
        }
        Ok(Self { layout, values })
    }
    pub fn layout(&self) -> &Layout {
        &self.layout
    }
    pub fn query(&self) -> &[f32] {
        &self.values[self.layout.query()]
    }
    pub fn passages(&self) -> &[f32] {
        &self.values[self.layout.passages()]
    }
    pub(crate) fn storage(&mut self) -> &mut [f32] {
        &mut self.values
    }
}
