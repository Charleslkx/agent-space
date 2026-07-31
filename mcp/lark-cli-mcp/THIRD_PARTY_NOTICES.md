# Third-party notices

The Docker image downloads the unmodified `lark-cli` binary from the official
[`larksuite/cli`](https://github.com/larksuite/cli) release selected by
`LARK_CLI_VERSION` in `Dockerfile`.

- Initial version: `1.0.81`
- License: MIT
- Upstream license: <https://github.com/larksuite/cli/blob/main/LICENSE>

The wrapper in this directory is separately licensed under MIT. Embedded Lark
Skill content is read at runtime from the upstream binary and is not copied into
this source package.
