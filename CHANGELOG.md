# CHANGELOG


## v0.2.3 (2026-03-16)

### Bug Fixes

- **ci**: Gate releases on CI success via workflow_run
  ([`85610f2`](https://github.com/therealahall/recommendinator/commit/85610f261109328720f6fd1a65e7295350719da6))


## v0.2.2 (2026-03-16)

### Bug Fixes

- **ci**: Include uv.lock in semantic-release commit assets
  ([`a908177`](https://github.com/therealahall/recommendinator/commit/a9081772a4be97e5715623e71e3598da36d1a745))


## v0.2.1 (2026-03-16)

### Bug Fixes

- **ci**: Include uv.lock in semantic-release version commit
  ([`b867560`](https://github.com/therealahall/recommendinator/commit/b8675602a60fe0fc92da5a51d880a3104d33c4a9))

- **ci**: Use semantic-release CLI and include uv.lock in version commit
  ([`0d4cbd7`](https://github.com/therealahall/recommendinator/commit/0d4cbd7b2990ef58f4e129621e8afbb94fa6258c))

### Chores

- **lockfile**: Regenerate uv.lock for v0.2.0 [skip ci]
  ([`27ec981`](https://github.com/therealahall/recommendinator/commit/27ec98134014b1b1c0a1d6272dc6dc1c3ca28792))


## v0.2.0 (2026-03-16)

### Bug Fixes

- Address review findings across credential storage
  ([`ca86fb2`](https://github.com/therealahall/recommendinator/commit/ca86fb284775ef668cfe294e0a499934b3738942))

- **storage**: Add debug logging to credential migration
  ([`53c5a56`](https://github.com/therealahall/recommendinator/commit/53c5a56cf95b29f93b9a4159b985267af8e719a9))

- **storage**: Default credential key path to DB directory
  ([`b83f0da`](https://github.com/therealahall/recommendinator/commit/b83f0da96cad1c8366f8607047df97f4f831599e))

- **storage**: Detect and recover from stale encrypted credentials
  ([`8b59893`](https://github.com/therealahall/recommendinator/commit/8b598936a73cba7475f94666ef1fcb8fc69e052b))

- **storage**: Remove unused sqlite_path param from _resolve_key_path
  ([`4db114c`](https://github.com/therealahall/recommendinator/commit/4db114cb85fbea948cdccda04932f549ff8ddc8e))

- **storage**: Restrict key directory permissions to 0700
  ([`49ac9dd`](https://github.com/therealahall/recommendinator/commit/49ac9dd438e9b242ede2ab693325112b4457d82e))

- **web**: Add debug logging to GOG token detection
  ([`cbb7c2c`](https://github.com/therealahall/recommendinator/commit/cbb7c2ce8aaf2563f85098f46191d9526b7e2b1e))

- **web**: Run credential migration on config hot-reload
  ([`9e23a5c`](https://github.com/therealahall/recommendinator/commit/9e23a5ca8cac55349ba08166ba6f66afb291914e))

### Chores

- Gitignore credential encryption key files
  ([`aa3017f`](https://github.com/therealahall/recommendinator/commit/aa3017f82c0bb2c2cc8a81fa1730bba6df61d4fa))

- **deps**: Add cryptography for credential encryption
  ([`50aa863`](https://github.com/therealahall/recommendinator/commit/50aa863500d12a4bf20833d9817e40079dff6247))

- **lockfile**: Regenerate uv.lock for v0.1.2 [skip ci]
  ([`45f6cf2`](https://github.com/therealahall/recommendinator/commit/45f6cf225906d4c951abec243949a9f536efe3a6))

### Documentation

- Add credentials table to ARCHITECTURE.md and improve GOG error message
  ([`c96f1f8`](https://github.com/therealahall/recommendinator/commit/c96f1f8183e1c5794c7248982c81a46209f42813))

- Update docs for encrypted credential storage
  ([`0e0d4b6`](https://github.com/therealahall/recommendinator/commit/0e0d4b601210f4510aefcc761a29715bd771015a))

### Features

- **cli**: Inject DB credentials and migrate on sync
  ([`cef12e3`](https://github.com/therealahall/recommendinator/commit/cef12e388ffa1271c25752d6997f615a972f1b54))

- **storage**: Add credentials table and CRUD functions
  ([`60b338b`](https://github.com/therealahall/recommendinator/commit/60b338bebb52b04b2b8578856995d413cb47c052))

- **storage**: Add Fernet-based credential encryption
  ([`92ae1fd`](https://github.com/therealahall/recommendinator/commit/92ae1fd2bace57998875de916c42843969aff381))

- **storage**: Auto-migrate sensitive credentials from config to DB
  ([`f288a71`](https://github.com/therealahall/recommendinator/commit/f288a711b0e27a82f610ece891ab8bfd72cd52a1))

- **storage**: Integrate credential encryption in StorageManager
  ([`48cc71d`](https://github.com/therealahall/recommendinator/commit/48cc71db1ef568be4139826a0831aa5b8f8d11b7))

- **web**: Inject DB credentials into plugin configs during sync
  ([`5bd2463`](https://github.com/therealahall/recommendinator/commit/5bd24639d6531896e74b71e1dc0619d01cdd6620))

- **web**: Run credential migration on app startup
  ([`ae83a63`](https://github.com/therealahall/recommendinator/commit/ae83a63704f731e8d96e5115984edd7cd5f65763))

- **web**: Save GOG refresh token to encrypted DB storage
  ([`c988579`](https://github.com/therealahall/recommendinator/commit/c988579d899d92a5f39677dcf53481dd5b3ba7b5))

- **web**: Update API endpoints for DB credential storage
  ([`1441811`](https://github.com/therealahall/recommendinator/commit/1441811a963cafe812e293d308170a0632f0eefe))

### Refactoring

- **storage**: Lazy-load CredentialEncryptor and defer cryptography import
  ([`0df3ca9`](https://github.com/therealahall/recommendinator/commit/0df3ca96937b89f6e0655ead933c8ee16381d81c))

- **web**: Remove dead transform_source_config function
  ([`0abff35`](https://github.com/therealahall/recommendinator/commit/0abff35a8c1fad44a06cceeeb3c1afd564e56866))

### Testing

- Update tests for DB credential storage
  ([`34325ca`](https://github.com/therealahall/recommendinator/commit/34325caaae35e8b7a6a130d247e5097528f182c9))

- **storage**: Add partial decrypt failure test for get_credentials_for_source
  ([`9556818`](https://github.com/therealahall/recommendinator/commit/955681878d6cd7aca843c1229d8a45d3199573d5))


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
