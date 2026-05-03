# CHANGELOG


## v0.9.3 (2026-05-03)

### Bug Fixes

- **ingestion**: Drop Steam status inference from playtime (closes #42)
  ([`2544bb9`](https://github.com/therealahall/recommendinator/commit/2544bb9b3f1fb6cd5136a43d83f2c797f9952164))


## v0.9.2 (2026-05-03)

### Bug Fixes

- **release**: Pass --no-vcs-release so psr does not pre-create release
  ([`ae84c7b`](https://github.com/therealahall/recommendinator/commit/ae84c7bead4e0a16521f95902bf5847988366c0d))


## v0.9.1 (2026-05-03)

### Bug Fixes

- **release**: Bundle docker-compose.yml at release creation
  ([`cde361a`](https://github.com/therealahall/recommendinator/commit/cde361aefb28be21babd539554598df99876ebf3))


## v0.9.0 (2026-05-03)

### Chores

- **agents**: Forbid review agents from writing code in their output
  ([`f3bed41`](https://github.com/therealahall/recommendinator/commit/f3bed413e0a98a852371a53cf3868b3eb120c03f))

### Documentation

- Document ROM Library plugin
  ([`d1dc877`](https://github.com/therealahall/recommendinator/commit/d1dc8776ebc0809ddc54058d1d3ad398d193ec14))

### Features

- **ingestion**: Add ROM Library scanner plugin
  ([`75928f8`](https://github.com/therealahall/recommendinator/commit/75928f840876e36911a82b0a5dfa658185100597))

- **roms**: Normalize underscores to spaces in cleaned titles
  ([`a2daedd`](https://github.com/therealahall/recommendinator/commit/a2daedd23b92b066a42052c0862d01aabb599510))

### Testing

- **roms**: Cover underscore edge cases in title cleaner
  ([`24bf6c4`](https://github.com/therealahall/recommendinator/commit/24bf6c4dd131a79eb44087a269204c9c4b757430))


## v0.8.1 (2026-05-01)

### Bug Fixes

- **recommendations**: Exclude in-progress items from displayed reasoning
  ([`38b7ace`](https://github.com/therealahall/recommendinator/commit/38b7ace6c18e88937f84539e77147dfd2582bbb0))

### Chores

- **github**: Simplify issue templates (fixes #48)
  ([`4a4a0a8`](https://github.com/therealahall/recommendinator/commit/4a4a0a8b3483649c63380b8d20a9a6d0fa72a678))


## v0.8.0 (2026-05-01)

### Bug Fixes

- **docker**: Anchor and escape ollama model-name regex
  ([`45467c8`](https://github.com/therealahall/recommendinator/commit/45467c8b83ebdda2304ee5a99c1288f7fdef0842))

- **docker**: Silence hadolint DL3008 on intentionally-unpinned build-essential
  ([`f908365`](https://github.com/therealahall/recommendinator/commit/f9083653a1b5f2b835f251c0e4341f09e48a9712))

### Continuous Integration

- Lint Dockerfiles, shell scripts, and compose manifests in PR builds
  ([`f107829`](https://github.com/therealahall/recommendinator/commit/f1078294adae68265c42cbbec1e8ab1324a703de))

- **docker**: Build images on PRs and publish multi-arch to GHCR on tags
  ([`c0562aa`](https://github.com/therealahall/recommendinator/commit/c0562aac4c0fd233ba4967b9708f11cba23c986e))

- **release**: Attach docker-compose.yml to release assets
  ([`076ff9d`](https://github.com/therealahall/recommendinator/commit/076ff9df021ab3f073b659e93f3a3061b6524296))

### Documentation

- Lead install instructions with Docker across project documentation
  ([`16280b0`](https://github.com/therealahall/recommendinator/commit/16280b0d7ad19f48c951c51826d10b3abda3fcac))

- **docker**: Add deployment guide for default and AI image variants
  ([`75f3715`](https://github.com/therealahall/recommendinator/commit/75f3715ec7432a71ac859fd9ec75afdd1ea484e3))

- **docker**: Require explicit app-ai service name for AI compose invocation
  ([`ada011a`](https://github.com/therealahall/recommendinator/commit/ada011a2b2386a5a1bef9542f955d16dfc3b759d))

### Features

- **docker**: Bootstrap containerized deployment with compose manifests
  ([`7de112b`](https://github.com/therealahall/recommendinator/commit/7de112b0c2c70809f2ada333e6439b3394aa7477))

- **web**: Add --reload CLI flag for development hot reload
  ([`e7d6a2b`](https://github.com/therealahall/recommendinator/commit/e7d6a2b4b140e3d25b45d151aa4aaae646ba496d))

### Testing

- **docker**: Cover entrypoint bootstrap and CONFIG_DIR validation
  ([`9065567`](https://github.com/therealahall/recommendinator/commit/906556729c7570785d7ddf09ce90c39577d59148))

- **web**: Cover reload-vs-production branching in main()
  ([`8677588`](https://github.com/therealahall/recommendinator/commit/867758889a545eb5fa34a8e4740564940dd114e1))


## v0.7.0 (2026-04-23)

### Bug Fixes

- **plugins**: Use consistent StorageManager type in validate_config signatures
  ([`03236bd`](https://github.com/therealahall/recommendinator/commit/03236bd7cb0f6aba6e886c0ad9b5cda541b4023c))

- **sync**: Forward storage param through validate_source_config to plugins
  ([`a7c7ffb`](https://github.com/therealahall/recommendinator/commit/a7c7ffb670a0121a2dd745728267d878543eb7ad))

- **validation**: Allow validate_config to check DB credentials for sensitive fields
  ([`9743eee`](https://github.com/therealahall/recommendinator/commit/9743eee2898201ba1cb4e6d01fa42bda18f1e6e1))

- **web**: Add disconnect button for connected OAuth sources
  ([`51f67cc`](https://github.com/therealahall/recommendinator/commit/51f67cc8536cc5f59ac0877ef3cf9e46d992bb8b))

- **web**: Mark sync as failed when plugin reports errors with no items
  ([`f25f0fd`](https://github.com/therealahall/recommendinator/commit/f25f0fde680e56d269d7eaea9b8538d91889bdb1))

### Chores

- Ignore .claude/worktrees/ local agent scratch directory
  ([`08269d0`](https://github.com/therealahall/recommendinator/commit/08269d038888424fe2c8b189356c679b973838fc))

- **agents**: Add parity-review agent for CLI/UI drift enforcement
  ([`da73345`](https://github.com/therealahall/recommendinator/commit/da733452e002a3280cd8ac3f3857683353a5b604))

### Documentation

- Document CLI command groups and anti-churn guidelines
  ([`045bd0b`](https://github.com/therealahall/recommendinator/commit/045bd0be8574c73a2504792bd984e163fa2f3700))

### Features

- **cli**: Add auth, chat, memory, profile groups for web parity
  ([`c23ff83`](https://github.com/therealahall/recommendinator/commit/c23ff83455480e4b26ef155cae9158f1b3e4e83c))

- **cli**: Add library command group (list, show, edit, ignore, export)
  ([`c772ecd`](https://github.com/therealahall/recommendinator/commit/c772ecd307dae2fd830c876e6cc411e59a2fbfeb))

- **cli**: Add status command for system health and feature flags
  ([`cc3f218`](https://github.com/therealahall/recommendinator/commit/cc3f2188540b5e64ed588ca87da8ec0746212459))

- **utils**: Add shared item_to_dict serialization helper
  ([`f9dc735`](https://github.com/therealahall/recommendinator/commit/f9dc7357c793b3a42b3b6f0d3f64d177da9789d6))

- **web**: Add OAuth disconnect endpoints and use shared serialization
  ([`6c357a5`](https://github.com/therealahall/recommendinator/commit/6c357a5354657a2231c5ffd45fda9a5adc30ed9a))

### Refactoring

- **web**: Polish disconnect button styling and tighten tests
  ([`04780ff`](https://github.com/therealahall/recommendinator/commit/04780ff9f45a8e48edb2bc0ed4a4ab60d850eff8))

### Testing

- Cover new CLI command groups and OAuth disconnect endpoints
  ([`e115ed3`](https://github.com/therealahall/recommendinator/commit/e115ed3e16c90d1b3a90e536f179814c320a9ab5))


## v0.6.0 (2026-04-10)

### Bug Fixes

- **data-page**: Hide sync status banner when no message is present
  ([`944f649`](https://github.com/therealahall/recommendinator/commit/944f649589b4427d1846a99d4dd2d5f25e59eaeb))

- **docker**: Increase Ollama healthcheck start period to 600s
  ([`46b4e9c`](https://github.com/therealahall/recommendinator/commit/46b4e9cf63d6d2100586e682d974a74b173c6cff))

- **frontend**: Add themed range slider and checkbox styles
  ([`c40b09a`](https://github.com/therealahall/recommendinator/commit/c40b09ac46160862b1b6d534eb0c12d0ffdbf556))

- **frontend**: Align show-ignored checkbox with filter dropdowns
  ([`165eb81`](https://github.com/therealahall/recommendinator/commit/165eb814f6fd2a257950cc95f590d793130b2302))

- **frontend**: Dynamically append theme stylesheet after Vite CSS bundle
  ([`a2b9724`](https://github.com/therealahall/recommendinator/commit/a2b97248cef20ed2400005edf8d2eaf08e5d43d2))

- **frontend**: Inline toolbar controls instead of stacked form-group layout
  ([`90a96ec`](https://github.com/therealahall/recommendinator/commit/90a96ec6fb1caa8d555e3eba411c283733ecc504))

- **frontend**: Pad library status dropdown and simplify enrichment actions
  ([`39bc9d8`](https://github.com/therealahall/recommendinator/commit/39bc9d880d70337d0b6887458b25bfde9451249b))

- **frontend**: Rename enrichment reset toggle to 'Reset Enrichment'
  ([`1657693`](https://github.com/therealahall/recommendinator/commit/165769363d64289b089f9d728fd988949b963b5b))

- **frontend**: Set Vite base to /static/dist/ so assets are served by FastAPI
  ([`12a08cd`](https://github.com/therealahall/recommendinator/commit/12a08cdb6c22e97489ba9853ceb969e9b65ec8ac))

- **frontend**: Show sync button on source cards without OAuth flows
  ([`5466c84`](https://github.com/therealahall/recommendinator/commit/5466c84ed80c51891d76e48ddbd4756191760e7c))

- **frontend**: Space recommendations toolbar with pills left, buttons right
  ([`6e9d23c`](https://github.com/therealahall/recommendinator/commit/6e9d23c0d3c793c1ce13d5e90805bdcd88b5418b))

- **frontend**: Style enrichment type dropdown to match other selects
  ([`2a947f6`](https://github.com/therealahall/recommendinator/commit/2a947f6c5ee39e6b39b2807efc983598d33ce77f))

- **frontend**: Unify toolbar pattern across all pages with dividers
  ([`4428fe5`](https://github.com/therealahall/recommendinator/commit/4428fe538b5fa0eb0f40ed6f65c2f0a115f7b3ea))

- **preferences**: Apply theme only on save, not on dropdown change
  ([`453829f`](https://github.com/therealahall/recommendinator/commit/453829fe5a1ae0a548ba86a439fc0535e03fce06))

- **release**: Skip CI on semantic-release version commits
  ([`e3d07d3`](https://github.com/therealahall/recommendinator/commit/e3d07d3470d03306b8cc8d3838d557adb62c42dd))

- **release**: Use inline commit_message string for semantic-release
  ([`ada5e41`](https://github.com/therealahall/recommendinator/commit/ada5e41b6eb823b27c0a7fd5cc67ef071bb98872))

- **security**: Add allowlists for where_clause, user_join, and user_filter in enrichment queries
  ([`5e4a171`](https://github.com/therealahall/recommendinator/commit/5e4a1710776b7effe0ca6272ba3d6e36fda65dfa))

- **status-bar**: Remove System ready banner and simplify component
  ([`f5d3898`](https://github.com/therealahall/recommendinator/commit/f5d38985e0eb1721983efd855b8447e8c4f222ab))

- **test**: Make SPA tests deterministic with synthetic dist/index.html
  ([`f7f4a19`](https://github.com/therealahall/recommendinator/commit/f7f4a195400148dafff0c2a792246de896955fc8))

### Chores

- Apply Black formatting to Python files
  ([`3fad80c`](https://github.com/therealahall/recommendinator/commit/3fad80ce633533f603aa1fbacdc33ec36e1c1f05))

- Standardize on pnpm for frontend package management
  ([`2ac1991`](https://github.com/therealahall/recommendinator/commit/2ac199167e77f008a6930b28c1005466d9578194))

- **agents**: Add accessibility-review agent and update CLAUDE.md
  ([`48d4731`](https://github.com/therealahall/recommendinator/commit/48d47317a56528fd15bfff69daa20a5c8f1b7928))

- **agents**: Add Vue 3/TypeScript and atomic design standards to code-review agent
  ([`291ede3`](https://github.com/therealahall/recommendinator/commit/291ede352aa8e50ab6dad6ad7eda4f40ca558c49))

- **build**: Add frontend build targets and gitignore entries
  ([`0b88740`](https://github.com/therealahall/recommendinator/commit/0b887404a0f00f3c75397c4f8b710d0c91069940))

### Code Style

- Format files flagged by black
  ([`9648f9e`](https://github.com/therealahall/recommendinator/commit/9648f9e81aebcd44561d7301622cb719f8ea85d4))

- **frontend**: Promote toolbar-select styles to global base.css
  ([`70a27ff`](https://github.com/therealahall/recommendinator/commit/70a27ff1ceb23d01dab601aed2a44ef19c3837cc))

- **rec-controls**: Simplify mobile layout CSS and remove duplicate stepper
  ([`2034e4e`](https://github.com/therealahall/recommendinator/commit/2034e4e021fa39d707cba6d07c510313722670d7))

### Continuous Integration

- Add frontend type-check and test steps to CI workflow
  ([`d30a39b`](https://github.com/therealahall/recommendinator/commit/d30a39b2e7610b8d1eda63c0c3b90a254689e3ac))

### Documentation

- Add accessibility-review agent to ARCHITECTURE.md and update CONTRIBUTING.md
  ([`4dd3782`](https://github.com/therealahall/recommendinator/commit/4dd37827e56a0048944398a57ea9d2794d7e8603))

- Remove stale web.theme references, fix agent component examples
  ([`135414e`](https://github.com/therealahall/recommendinator/commit/135414e962f874ec749f3f485b497e4b1ab03585))

- Update project documentation for Vue 3 frontend migration
  ([`5fcc910`](https://github.com/therealahall/recommendinator/commit/5fcc910dd7022c65bc69099ae8883c9011a4d6c5))

- **agents**: Add frontend performance rules to code-review agent
  ([`44875d0`](https://github.com/therealahall/recommendinator/commit/44875d018fd5bef5c2063232eec26df932555362))

### Features

- **components**: Add ToggleSwitch toggle control component
  ([`daf1b93`](https://github.com/therealahall/recommendinator/commit/daf1b938bffff8e52bf97440cada3975158b03b2))

- **components**: Add TypePills pill-based content type selector
  ([`126493f`](https://github.com/therealahall/recommendinator/commit/126493f8d711332bda22f09bf6dbd50cff675190))

- **components**: Rewrite LibraryFilters with pill toolbar layout
  ([`7388021`](https://github.com/therealahall/recommendinator/commit/73880216df4defee99c8d78a804c47aa01e4e58c))

- **components**: Update EnrichmentCard with TypePills and ToggleSwitch
  ([`bfd4878`](https://github.com/therealahall/recommendinator/commit/bfd487826b62d721b0912a8b9d49d4bbbb9210a8))

- **components**: Update RecControls with TypePills selector
  ([`5cbf2e8`](https://github.com/therealahall/recommendinator/commit/5cbf2e890bbe3adefd1f330f76d9251c4ee9f972))

- **data-page**: Wire per-source sync status and add progress bar ARIA
  ([`d50dfc5`](https://github.com/therealahall/recommendinator/commit/d50dfc5547b61a20362658dcd318a2bcc88d96b8))

- **data-store**: Track syncing source for per-source sync UI
  ([`55a1323`](https://github.com/therealahall/recommendinator/commit/55a13233fc1c3d3fb5f1809c6427bd0d7134e4ca))

- **docker**: Add frontend build stage with pnpm locked dependencies
  ([`532b49a`](https://github.com/therealahall/recommendinator/commit/532b49a17f025cf388c2a5a3989064c77728ac9f))

- **frontend**: Add accessibility infrastructure (focus trap, global styles, route focus)
  ([`9d23c6f`](https://github.com/therealahall/recommendinator/commit/9d23c6fe905ee17b08163dbfe5aae7547c5d40ff))

- **frontend**: Add atomic design atoms (ChatMessage, ChatInput, StarRating, ScorerSlider)
  ([`3f57bc1`](https://github.com/therealahall/recommendinator/commit/3f57bc101578352c837ed50e4f801b7091b6014c))

- **frontend**: Add atomic design molecules (9 components)
  ([`6c89a97`](https://github.com/therealahall/recommendinator/commit/6c89a976424d6b37429c6301812b042b1e733f1f))

- **frontend**: Add atomic design organisms (13 components)
  ([`e46d364`](https://github.com/therealahall/recommendinator/commit/e46d36462098aaf2a131c259ae319d8ba940daed))

- **frontend**: Add composables, types, constants, and format utilities
  ([`60a99ed`](https://github.com/therealahall/recommendinator/commit/60a99ed67c0c9b137a866de14f84b2d6f0871ede))

- **frontend**: Add CSS design system with Tailwind v4 theme mappings
  ([`5431a76`](https://github.com/therealahall/recommendinator/commit/5431a7692f7b009ffeb2a1a30e56081985428f40))

- **frontend**: Add mobile-responsive layout to LibraryFilters
  ([`bf0c9e7`](https://github.com/therealahall/recommendinator/commit/bf0c9e721846f847a7858282fa08f0a6d196622b))

- **frontend**: Add mobile-responsive layout to RecControls
  ([`dda2241`](https://github.com/therealahall/recommendinator/commit/dda2241b651184c87682ec4371e8dbb3b9ab1b4f))

- **frontend**: Add NumberStepper atom for recommendation count
  ([`fc08237`](https://github.com/therealahall/recommendinator/commit/fc082378de9e2cdcd901b6ef6d4bd8f09af1d807))

- **frontend**: Add page-level views for all 5 routes
  ([`5b3f53e`](https://github.com/therealahall/recommendinator/commit/5b3f53e0c10093e2713a9d9bc94a5c4f224a1ac7))

- **frontend**: Add TypeSelect atom component for mobile content type filtering
  ([`60dc0a1`](https://github.com/therealahall/recommendinator/commit/60dc0a1179283d7894d355d5bf97831a6327c516))

- **frontend**: Add Vite build tooling and TypeScript configuration
  ([`4fadf6b`](https://github.com/therealahall/recommendinator/commit/4fadf6b3dfaa61721015a81355f0d811e7e0c146))

- **frontend**: Add WCAG 2.1 AA accessibility to all Vue components
  ([`ccad9ce`](https://github.com/therealahall/recommendinator/commit/ccad9ce048fd65034261ec1696a61814a6feb921))

- **frontend**: Bootstrap Vue 3 app with router and Pinia stores
  ([`50f217e`](https://github.com/therealahall/recommendinator/commit/50f217e73475de8cbe34c720cc28ec572c43f40e))

- **frontend**: Extract CONTENT_TYPE_OPTIONS constant to shared module
  ([`ad2c1fd`](https://github.com/therealahall/recommendinator/commit/ad2c1fd11c0624ed841f39343523ff2c7320db50))

- **preferences**: Persist theme selection to backend per user
  ([`6c561ea`](https://github.com/therealahall/recommendinator/commit/6c561ea795c4e9e0422c48e290448519702566d4))

- **sync-card**: Add disabled prop with accessible label for per-source control
  ([`384325b`](https://github.com/therealahall/recommendinator/commit/384325bd7b4e333e183b35e97c5e37a297a1c234))

- **ui**: Add pill group, toggle switch, and dropdown menu styles
  ([`9e7f6c1`](https://github.com/therealahall/recommendinator/commit/9e7f6c10c82ed071c35b86961e45dcdcba7afc91))

- **web**: Serve Vue SPA from Vite build output at root endpoint
  ([`52db024`](https://github.com/therealahall/recommendinator/commit/52db024e4256822b28d9545a37abfa839f9104e1))

### Refactoring

- **format**: Extract truncate utility to shared format module
  ([`83ce8d6`](https://github.com/therealahall/recommendinator/commit/83ce8d6f1aadfe61507643f41dea468cb81f6a6d))

- **frontend**: Update TypePills to use shared CONTENT_TYPE_OPTIONS
  ([`604949c`](https://github.com/therealahall/recommendinator/commit/604949c274ac2e514c6b2da2951d6db7fbbf3bbf))

- **number-stepper**: Use aria-label attribute instead of custom prop
  ([`4fc012f`](https://github.com/therealahall/recommendinator/commit/4fc012fb48cdadda551e4c63f0220a0e1c73d0a5))

### Testing

- **format**: Add comprehensive coverage for all format utilities
  ([`2ec30e1`](https://github.com/therealahall/recommendinator/commit/2ec30e179c54386ab41b3ded44d75815fe94f7c3))

- **frontend**: Add NumberStepper tests and theme preference coverage
  ([`bf43691`](https://github.com/therealahall/recommendinator/commit/bf43691508e8715ede90bdfbf899a802bea0a096))

- **rec-controls**: Verify single stepper instance and aria-label propagation
  ([`8564b4b`](https://github.com/therealahall/recommendinator/commit/8564b4b7a2531275f0837f3e13e4078a6cc89980))

- **status-bar**: Add StatusBar component tests and statusMessage assertions
  ([`65c91a2`](https://github.com/therealahall/recommendinator/commit/65c91a2d915e7c442241f0619f0cb3ddf65f3580))

- **web**: Add SPA-aware root endpoint tests with deterministic paths
  ([`eacc137`](https://github.com/therealahall/recommendinator/commit/eacc1373833bcb4ebe38ebb204905509dc5ac7e7))


## v0.5.4 (2026-03-18)

### Bug Fixes

- **release**: Stage uv.lock in build_command for version commit
  ([`50c3f2e`](https://github.com/therealahall/recommendinator/commit/50c3f2e74fe450c26ad45b01295f739d17f9aee8))

### Chores

- Sync uv.lock with current version
  ([`32cc839`](https://github.com/therealahall/recommendinator/commit/32cc8395f48781f9332ccb0a6082270d0fcbe2a4))


## v0.5.3 (2026-03-18)

### Bug Fixes

- **release**: Force lockfile update for version bumps
  ([`30033d3`](https://github.com/therealahall/recommendinator/commit/30033d3ed2d1b8b8df35f59df247efcf29cf2fb5))


## v0.5.2 (2026-03-18)

### Bug Fixes

- **release**: Remove uv.lock from GitHub Release assets
  ([`5a43c0a`](https://github.com/therealahall/recommendinator/commit/5a43c0a92de989dda406a4854b24ed618fda67cb))


## v0.5.1 (2026-03-18)

### Bug Fixes

- **cli,web**: Clarify that recommendations are based on unconsumed items
  ([`2e6c3b0`](https://github.com/therealahall/recommendinator/commit/2e6c3b0082a27f290b109800e316b81f6196b97c))

- **conversation**: Prevent LLM hallucination when zero recommendations exist
  ([`01f57f8`](https://github.com/therealahall/recommendinator/commit/01f57f88e2abcf3a0e0133af50909cb00ecc2fc5))

### Testing

- **cli,web**: Add regression tests for empty-results messaging
  ([`7fc0a47`](https://github.com/therealahall/recommendinator/commit/7fc0a477bca03a487f135cf20dfa899b677a4e52))

- **conversation**: Add regression tests for LLM hallucination prevention
  ([`34a38b6`](https://github.com/therealahall/recommendinator/commit/34a38b63482ae39ff096da1bb07e04fe8a868130))


## v0.5.0 (2026-03-18)

### Bug Fixes

- **sources**: Persist rotated refresh tokens in GOG and Epic plugins
  ([`9c61a04`](https://github.com/therealahall/recommendinator/commit/9c61a045db1c0985c2fd504b835ffef1413b63b8))

- **web**: Use static error message for invalid content type
  ([`c21352c`](https://github.com/therealahall/recommendinator/commit/c21352cb89e16e239e6a3a80f09594bb120828ac))

### Documentation

- Document token rotation handling for OAuth plugins
  ([`f531a79`](https://github.com/therealahall/recommendinator/commit/f531a796a531c71128ac857fc4cefdd2aba950fa))

### Features

- **plugin_base**: Define CredentialUpdateCallback type
  ([`e318a88`](https://github.com/therealahall/recommendinator/commit/e318a88113dae5fcb3381809c09c45c45095b473))

- **sync**: Inject credential rotation callback into plugin config
  ([`3c5121f`](https://github.com/therealahall/recommendinator/commit/3c5121f29986216bdccf21139a2e23794266c314))

### Testing

- **sync**: Add regression tests for token rotation persistence
  ([`84d50c6`](https://github.com/therealahall/recommendinator/commit/84d50c6acc03835f625d9728925bc051db766477))


## v0.4.0 (2026-03-18)

### Documentation

- Document version display and update detection feature
  ([`6c9cc5e`](https://github.com/therealahall/recommendinator/commit/6c9cc5edbedf9c35b172fb2c65e05684f93ca022))

### Features

- **web**: Add version display in sidebar and cache busting for static assets
  ([`82a9f8a`](https://github.com/therealahall/recommendinator/commit/82a9f8a52183b31f932f9f64fdf07822acbf38fd))

- **web**: Add version polling and stale UI detection
  ([`0da2697`](https://github.com/therealahall/recommendinator/commit/0da2697595793006f76a302457f7467086538c5a))

### Testing

- **web**: Add tests for version display and cache busting
  ([`99402a4`](https://github.com/therealahall/recommendinator/commit/99402a400fa6e889ec9d4372d46b862e38c70f87))


## v0.3.0 (2026-03-17)

### Chores

- **dev**: Clarify pre-commit agent re-run workflow
  ([`2e4a461`](https://github.com/therealahall/recommendinator/commit/2e4a46169905727cb1402ea44fbab3c30f3ea4d1))

### Documentation

- Update Epic Games setup for web UI OAuth flow
  ([`40dd0bd`](https://github.com/therealahall/recommendinator/commit/40dd0bd349dc4d882d57a0949118675dec4f765f))

### Features

- **web**: Add Epic Games OAuth authentication service
  ([`8218aa3`](https://github.com/therealahall/recommendinator/commit/8218aa36d8728990f3325d0413aaaa2038e3850a))

- **web**: Add Epic Games OAuth endpoints and UI handlers
  ([`9552efd`](https://github.com/therealahall/recommendinator/commit/9552efd2596b017215e258c3fd8e7fb071b20842))

### Refactoring

- **epic**: Remove manual CLI setup path
  ([`c2db2f0`](https://github.com/therealahall/recommendinator/commit/c2db2f0ac38add22c36f5552098143aa59d2324b))


## v0.2.4 (2026-03-17)

### Bug Fixes

- **storage**: Never silently delete unreadable credentials
  ([`851f76f`](https://github.com/therealahall/recommendinator/commit/851f76fa3ab682ba96fd229179db443d378f77ec))


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
