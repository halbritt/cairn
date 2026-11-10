//! Shared rank/embedding invocation custody. Collection follows owner drop.
use cairn_semantic_backend::assets::{AssetPaths, CleanupErrors, NativeFactory};

pub(super) trait CleanupReport {
    fn is_empty(&self) -> bool;
}
#[derive(Debug)]
pub(super) struct RunFailure<E, C> {
    pub(super) invocation: Option<E>,
    pub(super) cleanup: C,
}

pub(super) fn with_cleanup<E, C: CleanupReport>(
    invoke: impl FnOnce() -> Result<(), E>,
    collect: impl FnOnce() -> C,
) -> Result<(), RunFailure<E, C>> {
    // The driver returns (and drops its owners) before draining shared observers,
    // including on invocation failure. Keep both causes instead of using `?` here.
    let invocation = invoke();
    let cleanup = collect();
    if invocation.is_ok() && cleanup.is_empty() {
        Ok(())
    } else {
        Err(RunFailure {
            invocation: invocation.err(),
            cleanup,
        })
    }
}

#[derive(Debug)]
pub(super) struct NativeCleanup {
    pub(super) ort: Vec<cairn_semantic_ort::Error>,
    pub(super) math: Vec<cairn_semantic_math::Error>,
}
impl CleanupReport for NativeCleanup {
    fn is_empty(&self) -> bool {
        self.ort.is_empty() && self.math.is_empty()
    }
}
pub(super) type NativeFailure<E> = RunFailure<E, NativeCleanup>;

pub(super) fn invoke_native<E>(
    paths: AssetPaths,
    invoke: impl FnOnce(NativeFactory) -> Result<(), E>,
) -> Result<(), NativeFailure<E>> {
    let cleanup = CleanupErrors::default();
    with_cleanup(
        || invoke(NativeFactory::new(paths, cleanup.clone())),
        || NativeCleanup {
            ort: cleanup.ort.drain(),
            math: cleanup.math.drain(),
        },
    )
}
