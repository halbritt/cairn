//! Adapter for the rank owner's admitted, already first-pass occurrence bits.
use cairn_semantic_math::{
    normalization::{FirstPassOccurrences, SecondPassNorms},
    FullOccurrenceMatrix,
};
use cairn_semantic_rank::backend::{BackendError, RawMatrix};
use std::rc::Rc;

pub struct Norms {
    original: RawMatrix,
    pending: SecondPassNorms,
    owner: Rc<()>,
}
pub struct Normalized {
    pub(crate) matrix: FullOccurrenceMatrix,
    pub(crate) owner: Rc<()>,
}
pub(crate) fn operation(stage: &'static str, detail: impl std::fmt::Debug) -> BackendError {
    BackendError::Operation {
        stage,
        detail: format!("{detail:?}"),
    }
}
pub(crate) fn norms(vectors: &RawMatrix, owner: &Rc<()>) -> Result<Norms, BackendError> {
    let matrix = FirstPassOccurrences::from_rank_first_pass_bits(
        vectors.rows,
        vectors.columns,
        vectors.bits.clone(),
    )
    .map_err(|e| operation("rank first-pass admission", e))?;
    Ok(Norms {
        original: vectors.clone(),
        pending: matrix.norms_keepdims(),
        owner: Rc::clone(owner),
    })
}
pub(crate) fn any_zero(norms: &Norms, owner: &Rc<()>) -> Result<bool, BackendError> {
    if !Rc::ptr_eq(owner, &norms.owner) {
        return Err(operation("norm owner", "different backend session"));
    }
    Ok(norms.pending.any_zero_norm())
}
pub(crate) fn divide(
    vectors: RawMatrix,
    norms: Norms,
    owner: &Rc<()>,
) -> Result<Normalized, BackendError> {
    if !Rc::ptr_eq(owner, &norms.owner) || vectors != norms.original {
        return Err(operation(
            "norm matrix binding",
            "matrix bits/shape or session changed",
        ));
    }
    let matrix = norms
        .pending
        .divide_in_place()
        .map_err(|e| operation("second normalization", e))?;
    Ok(Normalized {
        matrix,
        owner: Rc::clone(owner),
    })
}

pub(crate) fn tokenizer_error(
    stage: &'static str,
    error: cairn_semantic_tokenizer::Error,
) -> BackendError {
    match error {
        cairn_semantic_tokenizer::Error::TextUtf8(source) => {
            BackendError::TextUtf8 { stage, source }
        }
        other => operation(stage, other),
    }
}
