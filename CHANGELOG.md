# CHANGELOG


## v0.1.2 (2026-03-15)

### Bug Fixes

- **web**: Address remaining review findings for config watcher
  ([`dfd6479`](https://github.com/therealahall/recommendinator/commit/dfd6479b41c8ff02dbc055fed5b0950375872ea7))

- **web**: Address review findings for config hot-reload
  ([`8c51e0e`](https://github.com/therealahall/recommendinator/commit/8c51e0e846dd88e4a004f39e8c8f2e576c527baa))

- **web**: Hot-reload config file changes without restart (fixes #9)
  ([`dcff5f4`](https://github.com/therealahall/recommendinator/commit/dcff5f43bd29b62024b6cfc8d6cd6aeec6dbf19c))

### Chores

- **lockfile**: Regenerate uv.lock after adding watchfiles dependency
  ([`d0c3a5b`](https://github.com/therealahall/recommendinator/commit/d0c3a5b9910a51b6f072f354deea252cca2aa035))

- **lockfile**: Regenerate uv.lock for v0.1.1 [skip ci]
  ([`cd0b265`](https://github.com/therealahall/recommendinator/commit/cd0b265849177b480a8ff14e1837e37034c35646))

### Refactoring

- **web**: Use module import for watchfiles and extract test helper
  ([`2c47714`](https://github.com/therealahall/recommendinator/commit/2c47714c07ba56f656ab0bc6a9671727344a61bb))


## v0.1.1 (2026-03-15)

### Bug Fixes

- **ci**: Pass GH_TOKEN to checkout for branch protection bypass
  ([`b118c16`](https://github.com/therealahall/recommendinator/commit/b118c16808c6539c9ad3456298abf0d08f825754))

- **ci**: Use GH_TOKEN secret for semantic release workflow
  ([`a641390`](https://github.com/therealahall/recommendinator/commit/a641390e47aeaf101417643f224ea799c12f6b5d))

- **ollama**: Change default base URL from localhost to ollama container (fixes #5)
  ([`ddac641`](https://github.com/therealahall/recommendinator/commit/ddac6418f4d795ad37d98ad6dcabd931e3413bd6))

- **registry**: Skip abstract plugin classes during discovery (fixes #7)
  ([`239b3dd`](https://github.com/therealahall/recommendinator/commit/239b3dd260e743725272409a7530d0e7e3046a90))

- **steam**: Address review feedback on None config fix
  ([`f6c786e`](https://github.com/therealahall/recommendinator/commit/f6c786efb190e1552461353623e6df99c55913de))

- **steam**: Handle None config values from YAML parsing (fixes #2)
  ([`5455d60`](https://github.com/therealahall/recommendinator/commit/5455d60501e50a8b6c1025cd50996c678492db38))

- **web**: Surface validation error details in sync endpoint response
  ([`940c6a4`](https://github.com/therealahall/recommendinator/commit/940c6a436060aa94636ca0e7dd812695e3bd9d26))

### Documentation

- Add enrichment setup guide, conversation guide, and fix documentation gaps
  ([#1](https://github.com/therealahall/recommendinator/pull/1),
  [`8def483`](https://github.com/therealahall/recommendinator/commit/8def4835ad297852a90fe609cc9d8eb8bb586da3))


## v0.1.0 (2026-03-14)

- Initial Release
