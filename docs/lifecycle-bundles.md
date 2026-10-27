# Installed lifecycle bundles

A lifecycle installation carries the complete implementation needed by its
memory, coordination, and inbox recall entry points, including the shared
hook admission module that decides whether any of them runs. The shared bundle
module owns the file inventory, staging, validation, and dependency loading. Installers
and current native fixtures use that inventory instead of maintaining separate
lists of helper files.

Installers write `lifecycle-bundle.json` after the complete file set. The staged
loader is marked as installed and requires that manifest; the source loader can
read adjacent development files. Only the loader receives that installation
marker, so staging does not change the memory engine's source digest.

Adding a runtime member changes the required inventory. Current staging and
validation tools require the current complete set. An older installed bundle
continues to use its own unchanged loader until an explicit complete upgrade;
new validators do not add missing members to an older selected bundle.

New entry points load their adjacent implementation. They do not search a source
checkout, a former worktree, or another installation, and they do not silently
substitute a retained ledger implementation. A missing or changed required file
is an explicit installation failure. For example, `pinned lifecycle bundle
changed` means the staged bytes no longer match their pins. Keep the affected
binding disabled and restage a complete selected release before validating and
activating it again.

## Identity and binding

The bundle's identity covers its complete file set. It is separate from the
[caller-origin implementation ID](client-observations.md), which continues to
identify the memory engine's own source bytes. The caller-origin wire shape is
unchanged.

Bound inbox profiles use `cairn.inbox-recall-binding/2`, which requires the
complete bundle. An older reader must reject that contract instead of accepting a partial
set of pins. Validation retains the bytes used for execution, including imported
helpers; a later file replacement must not silently select different code.
Modules loaded for one bundle must not come from another bundle's process-wide
import cache.

## Upgrade and rollback

Existing installed copies keep their existing behavior until explicitly
upgraded. This change does not deploy code, rewrite credentials, move state, or
change account and collection bindings.

Keep the existing drain and restart procedure. Ordinary capture invocations must
finish before switching engines. For bound inbox recall, stage a complete pinned
set with activation disabled, validate it, and use the existing activation
procedure. Previously enabled callbacks and attempts must settle before their
pinned files are restored. Restart long-lived hosts and watchers as their
integration instructions require. This does not provide mixed-version fencing
or uninterrupted rolling upgrades.

An installer refuses to replace code used by an enabled inbox binding. Disable
the binding and settle its existing work before reinstalling, then regenerate
pins and validate the complete set before enabling it again. Custom state paths
and existing configuration fields survive the explicit reinstall.

Retain prior bundles for an explicit rollback. Automatic pruning is outside this
change. Historical frozen trials retain their original source pins; they are not
silently refreshed with current helper files.

[Prospective trials](plans/task-evaluation.md) select an already staged bundle
with an explicit sidecar pin. The [historical weekly exporter](weekly-runner-reconciliation.md)
retains its two-file contract and refuses current bundle code; selecting two
source files cannot select their dependencies.

## Verification

Installed-entry tests must exercise staged files with the source checkout
unavailable. Loading an installer's source-side engine does not establish that
the installed entry point can import its dependencies. Check missing and changed
helpers, bundle isolation, and execution of validated bytes alongside the existing
caller-origin, activation, and grant-accounting tests.
