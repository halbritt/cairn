//! Explicit embedding entry. Source publication does not admit native execution.
#[path = "entry/cleanup.rs"]
mod cleanup;
#[path = "rank_entry/plan.rs"]
mod plan;

use cairn_semantic_backend::{
    assets::AssetPaths,
    embedding_response::{run_embedding, InvocationError},
};
use cleanup::{invoke_native, NativeFailure};
use plan::{LaunchPlan, Mode, PlanError};
use std::{
    ffi::OsString,
    io::{self, BufRead, Write},
    process::ExitCode,
};

#[derive(Debug)]
enum EntryFailure<E> {
    Plan(PlanError),
    Invocation(E),
}

fn embedding_paths(args: impl IntoIterator<Item = OsString>) -> Result<AssetPaths, PlanError> {
    // Reuse the existing six-path parser and its OS-string/error policy. Rank's
    // optional stream mode is explicitly unavailable in the embedding entry.
    let plan = LaunchPlan::parse(args, None)?;
    match plan.mode {
        Mode::RankOnce => Ok(plan.paths),
        Mode::Stream { .. } => Err(PlanError::UnknownArgument("--stream".into())),
    }
}

fn dispatch<E>(
    args: impl IntoIterator<Item = OsString>,
    invoke: impl FnOnce(AssetPaths) -> Result<(), E>,
) -> Result<(), EntryFailure<E>> {
    let paths = embedding_paths(args).map_err(EntryFailure::Plan)?;
    invoke(paths).map_err(EntryFailure::Invocation)
}

fn run_native<R: BufRead, W: Write>(
    paths: AssetPaths,
    input: &mut R,
    output: &mut W,
) -> Result<(), NativeFailure<InvocationError>> {
    invoke_native(paths, |factory| run_embedding(input, output, factory))
}

fn invocation_diagnostic(error: &InvocationError) -> String {
    // InvocationError also owns raw request bytes: never format that whole value.
    format!(
        "embedding invocation failed after {} bytes: {:?}",
        error.written, error.failure
    )
}

fn main() -> ExitCode {
    match dispatch(std::env::args_os().skip(1), |paths| {
        let input = io::stdin();
        let output = io::stdout();
        let mut input = input.lock();
        let mut output = output.lock();
        run_native(paths, &mut input, &mut output)
    }) {
        Ok(()) => ExitCode::SUCCESS,
        Err(EntryFailure::Plan(error)) => {
            eprintln!("invalid embedding launch plan: {error:?}");
            ExitCode::from(2)
        }
        Err(EntryFailure::Invocation(error)) => {
            if let Some(invocation) = error.invocation {
                eprintln!("{}", invocation_diagnostic(&invocation));
            }
            for problem in error.cleanup.ort {
                eprintln!("ORT cleanup failed: {problem:?}");
            }
            for problem in error.cleanup.math {
                eprintln!("math cleanup failed: {problem:?}");
            }
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
#[path = "embed_entry/tests.rs"]
mod tests;
