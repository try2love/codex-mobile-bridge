//! Bridge supports Windows 10+, as required by its Electron and Rust runtimes.
//! Upstream's Windows 7 linker adapter downloads unpinned VC-LTL/YY-Thunks during
//! builds. Retain the normal supported Rust/MSVC linker instead of those shims.
pub fn thunk() {}
