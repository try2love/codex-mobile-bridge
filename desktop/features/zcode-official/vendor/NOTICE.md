This directory contains a build of selected ZCode client RPC, acknowledged relay,
shared protocol, and conversation projection sources from zai-org/ZCode, revision
`a9fe401eede6e6e5e01be9048679c206fe6f02d9` (Apache License 2.0).
The original source files are listed in `manifest.json`. Their algorithms are
bundled without protocol changes. `scripts/build-zcode-official.mjs` supplies the
entry point and resolves workspace imports; production logging is disabled by
the upstream production build flag. Copyright belongs to the upstream authors.

The bundle also contains Zod 4.6.5, copyright Colin McDonnell, licensed under MIT.
See `LICENSE.ZCode` and `LICENSE.zod` for the complete license texts.

Rebuild using the pinned official source checkout and temporary build-only
dependencies `esbuild@0.28.1` and `zod@4.6.5`. None is installed at application
runtime, and no upstream authentication state, user content, or API secret is
included in this bundle.
