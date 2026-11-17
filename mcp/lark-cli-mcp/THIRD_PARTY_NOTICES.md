# Third-party notices

The Docker image downloads the unmodified `lark-cli` binary from the official
[`larksuite/cli`](https://github.com/larksuite/cli) release selected at first installation from the latest stable GitHub release.

- Installed release: recorded locally in `deploy/lark-cli.version`; normal rebuilds retain it. The record is not distributed with source.
- License: MIT
- Upstream license: <https://github.com/larksuite/cli/blob/main/LICENSE>

The wrapper in this directory is separately licensed under MIT. Embedded Lark
Skill content is read at runtime from the upstream binary and is not copied into
this source package.
