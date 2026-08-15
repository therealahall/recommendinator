# CHANGELOG


## v0.35.0 (2026-08-15)

### Bug Fixes

- Close the review gate findings
  ([`4abc79b`](https://github.com/therealahall/recommendinator/commit/4abc79b924a9a036c4abff8647125d139907afc1))

- **enrichment**: Discover providers instead of listing them
  ([`ba7a9ea`](https://github.com/therealahall/recommendinator/commit/ba7a9ea03c7577fdd42ca38b31600ae424a7fc0f))

- **logging**: Keep console records the stream codec cannot encode
  ([`b55b5e8`](https://github.com/therealahall/recommendinator/commit/b55b5e801a36e6f7e3abc1807c8457b5309bc3b3))

- **registry**: Name the module, not a plugin type, when a private import fails
  ([`2e47b95`](https://github.com/therealahall/recommendinator/commit/2e47b95e28b02d9828e46bad8fcf4b8869887158))

- **storage**: Stop relabelling a source legitimately named goodreads
  ([`441d571`](https://github.com/therealahall/recommendinator/commit/441d571532b13d4be6b7dbf8946bb1b9f65351b2))

### Features

- Drop the AI features
  ([`baef05c`](https://github.com/therealahall/recommendinator/commit/baef05c226a773aafd8a14a144d70d9e5e779a9a))

### Refactoring

- Clear the residue the deletions left behind
  ([`40d26b3`](https://github.com/therealahall/recommendinator/commit/40d26b3dbd81f2df5d0834b293da6bf31cbfc996))

- Drop the SQL guards and registry API nothing reaches
  ([`546bcb0`](https://github.com/therealahall/recommendinator/commit/546bcb01241a9be027f02b3a8a8bf60fd515a11b))

- Finish the AI removal at the seams
  ([`08da290`](https://github.com/therealahall/recommendinator/commit/08da290abb8730df33289c74d55275c2988e9f7a))

- **frontend**: Drop the meta-test frameworks and Tailwind
  ([`f173d75`](https://github.com/therealahall/recommendinator/commit/f173d75e86f5cb51961ec0cb132ea1299a991ecf))

### Testing

- Stop pinning the exact contrast ratios
  ([`61d9f06`](https://github.com/therealahall/recommendinator/commit/61d9f06570cabedebac0826966c86f98e530c333))

### Breaking Changes

- Chat, memories and embeddings are gone, along with the recommendinator-ollama image and the ai
  install extra.


## v0.34.1 (2026-08-15)

### Bug Fixes

- **goodreads_rss**: Use the feed path Goodreads still serves signed out
  ([`cca3aa5`](https://github.com/therealahall/recommendinator/commit/cca3aa51f0b92523d1bc640d60513ad086f3c970))

- **sources**: Close the review findings on the credential and TLS work
  ([`fefa17e`](https://github.com/therealahall/recommendinator/commit/fefa17e1114c9571dc1955635f5f72bb24216c68))

- **sources**: Give Radarr and Sonarr a verify_ssl toggle, and stop following redirects off the
  origin
  ([`86b2e57`](https://github.com/therealahall/recommendinator/commit/86b2e57434f3d9ba2aa1f89565ca0a16fe9c0228))

- **sources**: Stop destroying a credential when the host has not changed
  ([`e3d9a09`](https://github.com/therealahall/recommendinator/commit/e3d9a09ba9b52130ca260dda374f851479d87689))

- **web**: Say the error list is errors, and keep it bounded
  ([`17209b9`](https://github.com/therealahall/recommendinator/commit/17209b9f06ae073d34bdb0c4a3ca68ab7f5611fb))

- **web**: Show a source's real defaults, and stop saving them back
  ([`2d873b1`](https://github.com/therealahall/recommendinator/commit/2d873b1e81d08a0c4fba55c05ca92329e7e83e34))

- **web**: Show which source failed and why, not a count
  ([`2c81852`](https://github.com/therealahall/recommendinator/commit/2c818527bba981698b6ef55eeb9a95d51adc659f))

### Refactoring

- Make the code say what the comments were saying
  ([`e6417d3`](https://github.com/therealahall/recommendinator/commit/e6417d307a5ce2b27af6b86b6f352fc96f6284ff))

### Testing

- Pin that the error cap applies per source
  ([`ebc504c`](https://github.com/therealahall/recommendinator/commit/ebc504c26f239a92cee873a85ca9906af92f8e75))


## v0.34.0 (2026-08-14)

### Bug Fixes

- **cli**: Match the web's error disclosure, and clean json stdout
  ([`6334a09`](https://github.com/therealahall/recommendinator/commit/6334a09d5d9ab38ea59f3a6b79f5e407dbd78414))

### Chores

- **agents**: Stop vendoring the shared review agents
  ([`b633bb2`](https://github.com/therealahall/recommendinator/commit/b633bb2bd0f7a91e5c0563486fcec07ab8779c6d))

### Refactoring

- **agents**: Fold test-review into code-review, and brief for judgement
  ([`d164e11`](https://github.com/therealahall/recommendinator/commit/d164e1125e1f422dbf5847ab6c7bf48991827f7c))

### Breaking Changes

- **cli**: --verbose moved to the root group.


## v0.33.0 (2026-08-14)

### Bug Fixes

- **a11y**: Stop six components cancelling the one focus ring
  ([`cd7a81b`](https://github.com/therealahall/recommendinator/commit/cd7a81b9e4c483e091f065b57ca582d514d99571))

- **a11y**: Stop the forms blurring the button the user just pressed
  ([`21be09f`](https://github.com/therealahall/recommendinator/commit/21be09f6854ad217c57aa3f0c13b32f5557c1e6d))

- **frontend**: Say why a blank name will not save
  ([`d899f26`](https://github.com/therealahall/recommendinator/commit/d899f26652f988bd97861c055a83f1d9e0d8bb35))

- **frontend**: Stop the dev server watching the agent worktrees
  ([`e745142`](https://github.com/therealahall/recommendinator/commit/e7451421aedc4ab9d4cdd2bb6ab436fddaaf281d))

- **frontend**: Take the password floor from the server that enforces it
  ([`25bddb9`](https://github.com/therealahall/recommendinator/commit/25bddb9106b7d1d4d5cb37a20300ac4a802c9908))

- **storage**: Enforce the password and name rules where both interfaces reach
  ([`7826e3b`](https://github.com/therealahall/recommendinator/commit/7826e3b81b308a61427e726e9256008f22728dcd))

- **web**: Drop the instance-wide login ceiling
  ([`a187384`](https://github.com/therealahall/recommendinator/commit/a18738472b1b315df823bb84806bb557824325e8))

- **web**: Make the session roll, bound the throttle, refuse cross-origin writes
  ([`bf90d25`](https://github.com/therealahall/recommendinator/commit/bf90d2503dcf9caa807d9eaebc2d6662ffb1c05b))

- **web**: Stop the throttle locking out the operator it protects
  ([`03f7049`](https://github.com/therealahall/recommendinator/commit/03f7049e033f53e4c6d0ce2845f92be063b23551))

### Documentation

- Describe signing in, not pasting a token
  ([`6815598`](https://github.com/therealahall/recommendinator/commit/6815598d55e01342c11c8926e055c63f0c582628))

- Explain the 403 a cross-origin write gets
  ([`ac224af`](https://github.com/therealahall/recommendinator/commit/ac224af9bc29fe3c1da317122ed131f1789c68db))

- Make deletion the first option a review finding considers
  ([`646461b`](https://github.com/therealahall/recommendinator/commit/646461bf26873d2385eb13629e139b61e30c7d2a))

- State the password minimum in the README first-run passage too
  ([`1d9cf90`](https://github.com/therealahall/recommendinator/commit/1d9cf907b375d624ec6ad947f1f47ede36fb5043))

- State the password rule and what an allowed origin actually reaches
  ([`ef35433`](https://github.com/therealahall/recommendinator/commit/ef35433e0efbeea7e42d8df5a636c3c097017ab4))

### Features

- **cli**: Add an account group for the break-glass password reset
  ([`69747f0`](https://github.com/therealahall/recommendinator/commit/69747f057f09ac1f0214cbf638e2cd20e702e1c1))

- **frontend**: Build the setup, login and account forms
  ([`70a2052`](https://github.com/therealahall/recommendinator/commit/70a2052c07300b41f7dfe52f62fbc07aa249fe7b))

- **frontend**: Sign in with a form instead of holding a token
  ([`8ae8e70`](https://github.com/therealahall/recommendinator/commit/8ae8e702ca8a154cb645c0536a9101fec2dc3d6b))

- **storage**: Give the single account a password and the browser a session
  ([`bf7508c`](https://github.com/therealahall/recommendinator/commit/bf7508ca5f388c0d11f148b4572bd67dc9727868))

- **web**: Replace the bearer token with a session cookie and a login
  ([`c769063`](https://github.com/therealahall/recommendinator/commit/c76906353a621368c2265cd36f98bf45aa5e7862))

### Testing

- **storage**: Let the password-stamp guard fail on the stamp
  ([`0ae93f1`](https://github.com/therealahall/recommendinator/commit/0ae93f1ff18dbc96068870acf07adbc9c4307bc8))

- **web**: Drive the auth stream end to end, upgrade path included
  ([`c0722c8`](https://github.com/therealahall/recommendinator/commit/c0722c87cd492432b46cd150a37dd82970d88b35))


## v0.32.0 (2026-08-13)

### Bug Fixes

- **frontend**: Bind the dev server to IPv4 loopback
  ([`0c5da73`](https://github.com/therealahall/recommendinator/commit/0c5da73e17e8c84e60565d09f25f7d4547be507c))

### Build System

- Declare httpx, which starlette's TestClient pulls into ten tests
  ([`3767373`](https://github.com/therealahall/recommendinator/commit/376737306ffd2f00784c0a471a6a27a7f154c8e1))

- Declare packaging, which the suite imports
  ([`e851f5d`](https://github.com/therealahall/recommendinator/commit/e851f5d3e04368d3cd77aabc143a88a9a1e9c3f5))

- Pin the toolchain to Python 3.11 and refresh two dependency floors
  ([`668edcd`](https://github.com/therealahall/recommendinator/commit/668edcd91d5323ee52a5bd4643194ce93c7617dc))

### Chores

- **review**: Gate parity on everything but docs, tests and tooling
  ([`aac091d`](https://github.com/therealahall/recommendinator/commit/aac091d235d15bebbd29a219e6839058dd47762a))

### Documentation

- Drop the user-count claim from the scale note
  ([`6b6e678`](https://github.com/therealahall/recommendinator/commit/6b6e67843241ba77b7751af69487f784bc679099))

- Point the CLI structure and anti-churn rule at the new package
  ([`6618796`](https://github.com/therealahall/recommendinator/commit/66187964eb310ecde0e518bab102ed8d35c2f15d))

- Say where CLI diagnostics go, and why the transports stay quiet
  ([`4d3ab62`](https://github.com/therealahall/recommendinator/commit/4d3ab620ffd4c5c0577f526fb19f8677439d7c46))

- State the project's scale so agents calibrate severity to it
  ([`85bf1bf`](https://github.com/therealahall/recommendinator/commit/85bf1bf579bd22781f01a760dc0f0d89ffb923bb))

- Stop advertising a Python version the package refuses
  ([`c1a1b43`](https://github.com/therealahall/recommendinator/commit/c1a1b43eea824f1e90fbf12b74769857b0910147))

- **agents**: Make test-review weigh a test's upkeep against what it proves
  ([`a6483da`](https://github.com/therealahall/recommendinator/commit/a6483daf3f98a996d21460a28a937c889d1190d5))

### Features

- **cli**: Give the CLI the logging it already told users to read
  ([`8e332da`](https://github.com/therealahall/recommendinator/commit/8e332daf93a05e6e64c6e8632aaf04a42268c8e4))

### Refactoring

- **architecture**: Move the shared services out of the interface packages
  ([`30dfe35`](https://github.com/therealahall/recommendinator/commit/30dfe355b9fb3ff8843ac41dbd7323ea01cb8bbc))

- **cli**: Split commands.py into one module per command group
  ([`8c4e7f3`](https://github.com/therealahall/recommendinator/commit/8c4e7f3f4393bcd2e77e05c37d9843efb549fb43))

- **export**: Give the CSV column tables and formula guard a neutral home
  ([`d4b0be3`](https://github.com/therealahall/recommendinator/commit/d4b0be3f24780899fa70ce4417d66b1c48b9c007))

### Testing

- Guard the interface boundary and the parity gate's own scope
  ([`01f1234`](https://github.com/therealahall/recommendinator/commit/01f12347a38174be796ce0359979940ed77ce6ba))

- Hold every dependency floor to what the lock resolves
  ([`6eab266`](https://github.com/therealahall/recommendinator/commit/6eab266be63fa710d35baaeb746ad2b237fc6b34))

- Hold the toolchain versions together wherever they are written
  ([`55718a3`](https://github.com/therealahall/recommendinator/commit/55718a3dc1d33aee32be4229dde305dc6a6c2a1c))

- Move the logging tests to the tree that owns the code
  ([`670e5ab`](https://github.com/therealahall/recommendinator/commit/670e5ab288e60029e0bc8acccc1f38fb8f8a5d39))

- Widen the parity-gate guard past the two interface trees
  ([`fd584cf`](https://github.com/therealahall/recommendinator/commit/fd584cfec5398227eab5410f009cc37a7968f814))


## v0.31.0 (2026-08-12)

### Bug Fixes

- **ci**: Let a tag push reach the workflow that publishes it
  ([`4220c5f`](https://github.com/therealahall/recommendinator/commit/4220c5f11bd80a7e513fcb3d7302cf84965e7dd7))

- **ci**: Publish only what the gate validated
  ([`9201c2c`](https://github.com/therealahall/recommendinator/commit/9201c2c47125f37af8987f4d3aac872b0343d520))

- **ci**: Require a release to descend from every alias it claims
  ([`d026df4`](https://github.com/therealahall/recommendinator/commit/d026df418651e2eaae04b9cc26eb44ed58d2bc87))

- **ci**: Serialise tag publishes and pin the guard's own wiring
  ([`351763f`](https://github.com/therealahall/recommendinator/commit/351763f148dea4976507720391a027ad56465c61))

- **docker**: Bind release publishing to commit history, not tag names
  ([`3888cf0`](https://github.com/therealahall/recommendinator/commit/3888cf074b34254f23aae77530ee2eb9c8b1404d))

- **docker**: Stop latest moving backwards, and unbreak a fresh ai install
  ([`29c9c50`](https://github.com/therealahall/recommendinator/commit/29c9c502137a34bc3c51fb7084d5dc12fd75a0b8))

- **security**: Key OAuth credentials on the source that owns them
  ([`e0e0850`](https://github.com/therealahall/recommendinator/commit/e0e0850dd0d9da5ecef36f3dcf61de4aefc91688))

- **security**: Key OAuth credentials on the source, in both interfaces
  ([`0233efc`](https://github.com/therealahall/recommendinator/commit/0233efc8e486dca8b242eea21137536b2680542b))

- **security**: Let a disabled source still revoke its token
  ([`c8b24d9`](https://github.com/therealahall/recommendinator/commit/c8b24d991f26f5b65c59c8c985e6ba604c043aee))

- **security**: Refuse an OAuth route a source running another plugin
  ([`8312f16`](https://github.com/therealahall/recommendinator/commit/8312f1610135de0847d36f6b9eedee11b2ea4ff6))

- **security**: Report the secrets a source holds, not the ones this call moved
  ([`ed17b1a`](https://github.com/therealahall/recommendinator/commit/ed17b1af63173bbe15fc2d5c2e6ccc0b34e552d7))

- **security**: Revoke a token unless another plugin's source owns its id
  ([`e589632`](https://github.com/therealahall/recommendinator/commit/e5896320e75f820fa4618926509e5a2ef7ae5c0e))

- **security**: Validate the CLI source id, and migrate credentials at startup
  ([`65b9622`](https://github.com/therealahall/recommendinator/commit/65b9622c4480b59206d393efd6f5d63e0f850bde))

- **web**: Let the newest connect-gate read win
  ([`862be79`](https://github.com/therealahall/recommendinator/commit/862be794667dc2dd0c63bd8514b63086e4791424))

- **web**: Re-read the connect gate after a write that moves it
  ([`71f70cf`](https://github.com/therealahall/recommendinator/commit/71f70cfe56e1de62a4b70772831682ce12ae1eeb))

- **web**: Retire a status read when its source is deleted
  ([`1637962`](https://github.com/therealahall/recommendinator/commit/1637962ac334f3b1c69433383ed5aae86c5fa713))

### Testing

- **security**: Make the migration tests fail when the migration is stubbed
  ([`3abf75f`](https://github.com/therealahall/recommendinator/commit/3abf75f4c5796e5d61cd69617b126d9dd5f90e28))


## v0.30.0 (2026-08-11)

### Bug Fixes

- **csv**: Neutralise formula characters on export, strip the guard on import
  ([`6e5d969`](https://github.com/therealahall/recommendinator/commit/6e5d96937723823487b4ce8c91923c31f1631db8))

- **security**: Close the remaining disclosure sinks
  ([`04530e8`](https://github.com/therealahall/recommendinator/commit/04530e8a9c6d4833ac9c86e55f61afb7d83e1395))

- **sync**: Key a rotated credential on the source id, and escape the log
  ([`14570e3`](https://github.com/therealahall/recommendinator/commit/14570e38396a6fcb7d826af66f410fbc5662d29c))

- **web**: Name UTF-8 at every text boundary
  ([`a55bf5c`](https://github.com/therealahall/recommendinator/commit/a55bf5cfecb66714d9add7c0ef9c6f5a501863bb))

- **web**: Stop the config doors describing what they refused
  ([`6af2317`](https://github.com/therealahall/recommendinator/commit/6af23174ddf4434d80be89b4d1d1056c36f59414))

### Chores

- **llm**: Delete generate_embeddings_batch
  ([`8c9b456`](https://github.com/therealahall/recommendinator/commit/8c9b456aa687a22fbc675981a603dc2555edf9c1))

### Documentation

- Cut the security-remainder additions back
  ([`1c230cf`](https://github.com/therealahall/recommendinator/commit/1c230cff0e5c28144f15635794d7055159044661))

- Record what each door reveals and which chain to break
  ([`48f1cf1`](https://github.com/therealahall/recommendinator/commit/48f1cf1e1a11ca2e741ac16732473203cf4b22b1))

### Features

- **storage**: Re-attribute content items to the source that owns them
  ([`fdd832e`](https://github.com/therealahall/recommendinator/commit/fdd832ebce293a8a3ed639fa6efaa3afc2112ac6))

- **utils**: One family of sanitisers for logs, prompts and rules
  ([`4769d12`](https://github.com/therealahall/recommendinator/commit/4769d12b8824d57cd0d3bf6ca513dab8b769cbeb))

### Refactoring

- **ingestion**: Make source identity framework-owned
  ([`dc1c8a8`](https://github.com/therealahall/recommendinator/commit/dc1c8a8dc62335b8aaac856d88baa89d78121884))

### Breaking Changes

- **ingestion**: Overriding transform_config or get_source_identifier now raises TypeError. Override
  transform_fields.

- **storage**: Content_items.source holds the source id, not a plugin name.


## v0.29.0 (2026-08-09)

### Bug Fixes

- **cli**: Say when a preference write was refused
  ([`c0e9c16`](https://github.com/therealahall/recommendinator/commit/c0e9c16164a9b8487d0731af54f1c6207dc10641))

- **docker**: Stop the healthcheck failing on its own auth
  ([`2fbbcbc`](https://github.com/therealahall/recommendinator/commit/2fbbcbc4fcdeb47e556997cc39c7b94a5ce2f5ce))

- **llm,conversation**: Read the LLM settings per call, not per boot
  ([`bbf9eab`](https://github.com/therealahall/recommendinator/commit/bbf9eab5eebe63cafe7c82522fcfa4fd39b8f09d))

- **settings**: Refuse an ollama host that only looks local
  ([`46920fa`](https://github.com/therealahall/recommendinator/commit/46920faeca78538a48483ee3043427a1722b9184))

- **sources**: Bind a stored secret to the host it was issued for
  ([`82f7995`](https://github.com/therealahall/recommendinator/commit/82f79954d5ea3c0194bc2343fb62383041a8008d))

- **web**: Bound what one request can ask the server to hold
  ([`4047242`](https://github.com/therealahall/recommendinator/commit/40472429f541285740043f4fd8c37225eceea327))

### Documentation

- Cut a third of what four review rounds added
  ([`23f90b9`](https://github.com/therealahall/recommendinator/commit/23f90b964d8eac66f3edc1d7be3c43c36029cc89))

- Describe the surface auth and containment left behind
  ([`4ed0fd1`](https://github.com/therealahall/recommendinator/commit/4ed0fd1c6db70220fa708237d185cb093355fd4b))

### Features

- **docker**: Let the operator choose the API token
  ([`ea79e2c`](https://github.com/therealahall/recommendinator/commit/ea79e2c3bfe79a30ef7cca0c5f6e01948c2d329f))

- **security**: Keep a file import inside the roots the operator named
  ([`302f453`](https://github.com/therealahall/recommendinator/commit/302f453591c04f625674ca8553cf50c58f296533))

- **web**: Ask for the token before unlocking the app
  ([`32733fa`](https://github.com/therealahall/recommendinator/commit/32733fa7c2067d0070a772e93eae0491543ea87e))

- **web**: Require a bearer token on every API route
  ([`82faaff`](https://github.com/therealahall/recommendinator/commit/82faaff2094d29332cf78c2073813d535644aaa8))

### Testing

- Refuse a connection to anywhere but loopback
  ([`6a4bf36`](https://github.com/therealahall/recommendinator/commit/6a4bf36ca2283537d1124a27220de22c234bfa5f))

- **web**: Read the annotation rather than a FastAPI internal
  ([`2fd55c9`](https://github.com/therealahall/recommendinator/commit/2fd55c94f9bb28894d803098e7e9d673d1ae8049))

### Breaking Changes

- **docker**: A fresh container exits until web.api_token is set.


## v0.28.0 (2026-08-08)

### Bug Fixes

- **ingestion,enrichment**: Publish a discovered registry in one swap
  ([`d4b259e`](https://github.com/therealahall/recommendinator/commit/d4b259ec237450a21fbb313ea833f9582376b35b))

- **storage**: Make a preference merge one operation
  ([`9465095`](https://github.com/therealahall/recommendinator/commit/9465095c2c176d3592e964cffccfbf38d30b8211))

- **web**: Build each lazy manager once, and start one enrichment job
  ([`1e152a0`](https://github.com/therealahall/recommendinator/commit/1e152a0dd0cdbfc001d39d3153271da314529099))

- **web**: Give the running config one serialiser instead of the event loop
  ([`60acadd`](https://github.com/therealahall/recommendinator/commit/60acadd80b43746645adb7fcbed89f93e310d86b))

### Documentation

- Name the threadpool rule and the four locks it made necessary
  ([`292631d`](https://github.com/therealahall/recommendinator/commit/292631dc8f313c74156556adfbd0abad9fdc1695))

### Refactoring

- **web**: Run every API handler in a threadpool behind declared guards
  ([`a395561`](https://github.com/therealahall/recommendinator/commit/a3955612394582478bdae3e71c9435d558ff2bf0))

### Breaking Changes

- **web**: Dependencies resolve before validation, so a down component answers 503 not 422, an
  unknown source with a bad body 404.


## v0.27.0 (2026-08-08)

### Bug Fixes

- **web**: Give one server state one answer
  ([`6dc3493`](https://github.com/therealahall/recommendinator/commit/6dc349371f133d2e4ee7719f0ac20cdb63f33c6b))

### Documentation

- Say which reads are guarded and which are meant to fall back
  ([`75097c9`](https://github.com/therealahall/recommendinator/commit/75097c98c6107e38aebeab8ac525ce30b8f8bf10))

### Testing

- **web**: Stop booting the real app during collection
  ([`b9eadfa`](https://github.com/therealahall/recommendinator/commit/b9eadfa39c031d0ea45ba795cff42fe902537f1b))

### Breaking Changes

- **web**: Endpoints that reported an uninitialised component as 500, 404 or a 200 with an empty or
  negative body now answer 503. A client checking for 500 should treat 503 as the transient case it
  always was.


## v0.26.1 (2026-08-08)

### Bug Fixes

- **recommendations**: Tire of a genre you finished, even unrated
  ([`5f7e620`](https://github.com/therealahall/recommendinator/commit/5f7e620a3e1e2b0f859c8f4c4cd0c2d2fcdd8514))

### Documentation

- Name both facts that outlive a rating
  ([`8766761`](https://github.com/therealahall/recommendinator/commit/8766761a50e24df275d7439e536f4ab8e9a93492))

### Testing

- **recommendations**: Cover the paths the widening opened
  ([`6e395d2`](https://github.com/therealahall/recommendinator/commit/6e395d2f79c6a6d3b7467137aae95ce980741d96))

- **storage**: Pin the two item sets apart
  ([`b1199bb`](https://github.com/therealahall/recommendinator/commit/b1199bb0193388cc20ff994b0e939f01843f2adf))


## v0.26.0 (2026-08-07)

### Bug Fixes

- **llm**: Recognise a creator whose name ends in punctuation
  ([`2a41c02`](https://github.com/therealahall/recommendinator/commit/2a41c02dd9f777528370fb9cbadf5cf5bfa05ded))

- **recommendations**: Apply settings changes without a restart
  ([`e789231`](https://github.com/therealahall/recommendinator/commit/e7892310057efdf9f3f3a980bbcab64f6ac882da))

- **recommendations**: Keep preference scores inside [-1, 1]
  ([`3951e27`](https://github.com/therealahall/recommendinator/commit/3951e271d8a43d4069c8f86e527540a866d957c1))

- **recommendations**: Key candidates by their library row, not a nullable id
  ([`91720cc`](https://github.com/therealahall/recommendinator/commit/91720cc8f719ee169abd0d6173c61375c6c86dfc))

- **recommendations**: Make the score shown the score explained
  ([`195e2ab`](https://github.com/therealahall/recommendinator/commit/195e2ab17de05d77008f323d216375a787b99d33))

- **recommendations**: Read game length from RAWG, not from your playtime
  ([`cfb2754`](https://github.com/therealahall/recommendinator/commit/cfb2754de73ed936d8b4bf83010e108f2cb5919e))

- **recommendations**: Score an up-next novella as next, not as read
  ([`65db211`](https://github.com/therealahall/recommendinator/commit/65db211847e7ebbc24886f7bc3dbc7fe529eddc1))

- **recommendations**: Stop trusting isdigit to mean int will work
  ([`583c933`](https://github.com/therealahall/recommendinator/commit/583c93303e049a8b49674381f242a644aac8afd9))

- **settings**: Give a request one configuration, not two or three
  ([`2e7fd72`](https://github.com/therealahall/recommendinator/commit/2e7fd7293a3912a3d01f741c5235bf9c67574ee6))

- **settings**: Stop mutating the running config under a live reader
  ([`cfebb7e`](https://github.com/therealahall/recommendinator/commit/cfebb7ec9e1fa86b7e7392a2a08d00370bc9e773))

### Documentation

- Correct four claims the last three commits outran
  ([`c498bc0`](https://github.com/therealahall/recommendinator/commit/c498bc07580bdf9cee6b57a53b4bfa9d84346efa))

### Performance Improvements

- **recommendations**: Index the signal set once instead of scanning it per candidate
  ([`b96d3a2`](https://github.com/therealahall/recommendinator/commit/b96d3a2aeb267e7e9bdd439cd3b8bba377c7cfe1))

- **recommendations**: Resolve references only for what is emitted
  ([`2e2586f`](https://github.com/therealahall/recommendinator/commit/2e2586f7f0ef6390207508c9777cd9579a3bea38))

### Refactoring

- **recommendations**: Give the emitted record a real type
  ([`89d42ea`](https://github.com/therealahall/recommendinator/commit/89d42ea380a80ccaa6095e98c39ae0cc0b74bd40))

- **recommendations**: Name the variety set honestly and seed the shuffle
  ([`42c129e`](https://github.com/therealahall/recommendinator/commit/42c129e1bc699c93f7a388c9d2c6e1c04e419965))

### Testing

- **recommendations**: Cover the LLM-only path, type exclusions and creators
  ([`e833f8a`](https://github.com/therealahall/recommendinator/commit/e833f8abfecf2e0edb3c6a52b5290a3ed0c01c44))

- **recommendations**: Fix three tests that had stopped proving anything
  ([`4cc97f9`](https://github.com/therealahall/recommendinator/commit/4cc97f920049b2ce1c3777db742092432f0fe1b5))

- **recommendations**: Pin today's ranking before the collapse
  ([`9113d8f`](https://github.com/therealahall/recommendinator/commit/9113d8fb602bfbe85489f83e54125a3b33dde22b))

- **settings**: Pin the guarantee the batched publish exists for
  ([`f6f2c66`](https://github.com/therealahall/recommendinator/commit/f6f2c66a8820d4a93d65abcc5a10a4bd021e0356))


## v0.25.2 (2026-08-07)

### Chores

- **agents**: Cut the review agents back to findings that matter
  ([`f06e1d5`](https://github.com/therealahall/recommendinator/commit/f06e1d5b31c7e07a27715cca39c0cd19e6b3dde7))

### Documentation

- **architecture**: Describe the one time migration scheme
  ([`45bf5f7`](https://github.com/therealahall/recommendinator/commit/45bf5f7ec0a536cb7dd5bd41b180c891d0031c1c))

- **security**: Say the git diff grant is unguarded here
  ([`403ce7c`](https://github.com/therealahall/recommendinator/commit/403ce7c9b0704ad4bbcf136fd762dfcfdd3efad9))

### Performance Improvements

- **storage**: Page the library list and the search in SQL
  ([`36bb721`](https://github.com/therealahall/recommendinator/commit/36bb7216c5d345403a782dcc35232be36517a7a5))

- **storage**: Run the one time repairs once, not on every open
  ([`8863112`](https://github.com/therealahall/recommendinator/commit/886311213bbd3ed68d541abb5491082f45982cbf))

### Testing

- **agents**: Stop pinning the shared guidance wording
  ([`3a4abfb`](https://github.com/therealahall/recommendinator/commit/3a4abfb557b55d44899eb4ba1b6e00e7cac52aaf))


## v0.25.1 (2026-08-04)

### Bug Fixes

- **cli**: Head the creator column Creator, not Author
  ([`4a74c0a`](https://github.com/therealahall/recommendinator/commit/4a74c0a5c6c337a16b029a015a255896e97b8dfe))

- **cli**: Label the creator row by content type in library show
  ([`f27850f`](https://github.com/therealahall/recommendinator/commit/f27850f39cc92407d7bddd3b10881a311a15056b))

- **models**: Drop blank entries from a joined name
  ([`969620e`](https://github.com/therealahall/recommendinator/commit/969620e0466b09156404455e898f9e61027d7d05))

- **models**: Fail at import when a content type has no field declaration
  ([`58f1b75`](https://github.com/therealahall/recommendinator/commit/58f1b75108527d2f6e5ae8d8e074e84077ca3b2e))

- **models**: Keep a null out of a joined creator name
  ([`f27914b`](https://github.com/therealahall/recommendinator/commit/f27914b630033ce9d30499dc2032125553347d35))

- **models**: Strip a name before joining it, not just before testing it
  ([`2d12081`](https://github.com/therealahall/recommendinator/commit/2d12081f80e3f93f811c55f8a621b92eab851cb7))

- **models,ingestion**: Keep what a text column cannot hold out of it
  ([`918ab5d`](https://github.com/therealahall/recommendinator/commit/918ab5dd65053d9c2dbf68897be87f7dc1dc589f))

- **models,ingestion**: Refuse a value a text column cannot hold
  ([`038f0e0`](https://github.com/therealahall/recommendinator/commit/038f0e0c2d3df0c100863d1f539a2170de29667d))

- **storage**: Fold a stranded company name onto its column
  ([`85c0b5b`](https://github.com/therealahall/recommendinator/commit/85c0b5b01be37ad2809137c34f54a71365282502))

- **storage**: Fold every stranded blob key onto its column, not just seasons
  ([`8326b41`](https://github.com/therealahall/recommendinator/commit/8326b41da08c6e0765014cc34959115e7f937415))

- **storage**: Guard the read path against a blob that is not an object
  ([`89ddbb7`](https://github.com/therealahall/recommendinator/commit/89ddbb7febe24ec5279cd77751ba406e975ca8e2))

- **storage**: Let a wrong column name raise instead of reading as absent
  ([`4740ab6`](https://github.com/therealahall/recommendinator/commit/4740ab6b3d50e2e4e840808fdca941f89c2fabbb))

- **storage**: Repair detail rows left in shapes no re-sync corrects
  ([`5c6d0d3`](https://github.com/therealahall/recommendinator/commit/5c6d0d347f27958d30ce6ad1ebcee2227c189bb4))

- **storage**: Repair stranded counts before merging duplicates
  ([`338096b`](https://github.com/therealahall/recommendinator/commit/338096b1bb811a44cf062cebccf4bdf9f5d215ae))

- **storage**: Repair the two shapes this pass was scoped for
  ([`4cbaf51`](https://github.com/therealahall/recommendinator/commit/4cbaf5199eedd72857b25ffdae75d5bb1a021a9a))

- **storage**: Weigh every spelling of a field when repairing a row
  ([`d27a611`](https://github.com/therealahall/recommendinator/commit/d27a6115eb117b85c5a076d961951a38b1466557))

- **storage,cli**: Point the missing extra message at uv, not pip
  ([`bd5401d`](https://github.com/therealahall/recommendinator/commit/bd5401d98a4964cd7d1fa80a564372559dd4eb5c))

- **storage,models**: Say what actually keeps the creator alias safe
  ([`e253f38`](https://github.com/therealahall/recommendinator/commit/e253f38002e946160f9240bdd8cd372deb19cb83))

- **storage,web**: Give every content type its creator back
  ([`a01c8c2`](https://github.com/therealahall/recommendinator/commit/a01c8c26394eb53bd5960228b87d8af853b7e30e))

### Chores

- **agents**: Stop document-review asking for more prose
  ([`6d8bee2`](https://github.com/therealahall/recommendinator/commit/6d8bee2567df9b8f49d9de2ba47c84325364f05a))

- **storage**: Drop two comments arguing a position that changed
  ([`86db13b`](https://github.com/therealahall/recommendinator/commit/86db13bd1a701e9f5f7f614d15f190ce53ffb280))

### Documentation

- Cut ARCHITECTURE, CLI and README down
  ([`f458d74`](https://github.com/therealahall/recommendinator/commit/f458d742e305c4a14a390cdec6a7a5f90f305457))

- Cut the contributor guides down
  ([`8f8bbec`](https://github.com/therealahall/recommendinator/commit/8f8bbec4c12dcef4c4094318343740f824255642))

- Cut the feature guides down
  ([`e4dfa29`](https://github.com/therealahall/recommendinator/commit/e4dfa29349fb74fb679dc6653f162272a07eed33))

- Cut the setup and operations guides down
  ([`e5531b5`](https://github.com/therealahall/recommendinator/commit/e5531b524b58637c2344e49b6ee4fdcd17622d90))

- Describe the field declaration and the detail shape repairs
  ([`d87f6ca`](https://github.com/therealahall/recommendinator/commit/d87f6ca5bd945b14285452842523e8746550663e))

- Describe the third detail shape the repair pass fixes
  ([`a729043`](https://github.com/therealahall/recommendinator/commit/a729043a85c1b6985e294dfa33eb479395a1d460))

- Record the repair ordering and qualify the creator export claim
  ([`4947f1e`](https://github.com/therealahall/recommendinator/commit/4947f1ef478f37efba6461d7e65e84f9d6a8931e))

- Record where a stored item carries its creator
  ([`db2508e`](https://github.com/therealahall/recommendinator/commit/db2508e4eea3c72a0ba57cfe1872621316785356))

- Tell plugin authors what a text column refuses, and count the list
  ([`78d344b`](https://github.com/therealahall/recommendinator/commit/78d344bd92b4e440487fa840733690d739465eee))

- **architecture**: Describe the two detail shape repairs
  ([`aa7e385`](https://github.com/therealahall/recommendinator/commit/aa7e385113b6c5b4569ff1ae6b008e48dacddaf7))

- **data-sources**: Say what the platform repair fixes and what it misses
  ([`bd640d3`](https://github.com/therealahall/recommendinator/commit/bd640d37c012064909adf940811b07ae266bbb0e))

### Refactoring

- **cli**: Name the recommend loop variable
  ([`9f840d8`](https://github.com/therealahall/recommendinator/commit/9f840d85cea5b2463262bebfed6b686f99148f6f))

- **storage**: Declare each detail table's fields once
  ([`c71fb4d`](https://github.com/therealahall/recommendinator/commit/c71fb4da48c76307a30e5247252ef939f0b86b16))

- **web,ingestion**: Index the field declaration instead of defaulting around it
  ([`cffa28e`](https://github.com/therealahall/recommendinator/commit/cffa28e0e6f5cf79d736eb0f60618fde71fbf527))

### Testing

- Stop the install instruction scan depending on the working tree
  ([`1f56d8a`](https://github.com/therealahall/recommendinator/commit/1f56d8a20b536c1828be32186707632d38d870a1))

- **export**: Check the JSON columns too, and keep catching duplicates
  ([`23d3cd0`](https://github.com/therealahall/recommendinator/commit/23d3cd0429ac928d916eb44b7af4d329c6beadb7))

- **export**: Check which columns are written, not what order
  ([`ff88a33`](https://github.com/therealahall/recommendinator/commit/ff88a337fa575406e820de6351b6e5b40f059997))

- **storage**: Pin what the undeclared type regressions actually raise
  ([`bcce0fb`](https://github.com/therealahall/recommendinator/commit/bcce0fb3e69a3ff20e8d703113be3a2a8b9fab58))

- **storage**: Prove an empty value leaves its column open
  ([`e584496`](https://github.com/therealahall/recommendinator/commit/e584496480929a5eb5878e85a6b2b7530da87939))

- **storage**: Show the creator exemption costs every save, not one
  ([`108c0df`](https://github.com/therealahall/recommendinator/commit/108c0df0815ce827152ec75f20dd83762cdb6aa2))


## v0.25.0 (2026-08-04)

### Bug Fixes

- **cli,web,chat**: Send explicit user actions through the doors
  ([`ce37747`](https://github.com/therealahall/recommendinator/commit/ce3774750b7529f200cceb31bdd8426687febe12))

- **dates**: Date completions by the host's calendar day, not UTC's
  ([`e9bd1e1`](https://github.com/therealahall/recommendinator/commit/e9bd1e1a5cc2d07133bf80821a61a4299c622213))

- **docker**: Install tzdata and pass TZ into the app containers
  ([`160a51e`](https://github.com/therealahall/recommendinator/commit/160a51ead1fffcfb8ec70ae90b385a362c0cbb62))

- **enrichment**: Retry a provider failure instead of settling it
  ([`80944f5`](https://github.com/therealahall/recommendinator/commit/80944f53f588327d166115304ef8c1137b3cb5a9))

- **ingestion**: Store imported metadata under the keys the library reads
  ([`02d88b3`](https://github.com/therealahall/recommendinator/commit/02d88b370f667d2455cb08472d204d2f166cc915))

- **search**: Find non Latin titles, and set a bound on the term
  ([`30e8660`](https://github.com/therealahall/recommendinator/commit/30e8660267adfdc62bf298f60b26f503f7ca55a6))

- **storage**: Put every write to a user owned field through a door
  ([`046ee07`](https://github.com/therealahall/recommendinator/commit/046ee076a0d9988d1cd5f4065e6e5910a2684d79))

### Chores

- **agents**: Give every review agent the same shared guidance
  ([`a3d8535`](https://github.com/therealahall/recommendinator/commit/a3d85354eb5f49035340b71128e46f2334045b72))

- **review-gate**: Commit the mandated agents and verify they load
  ([`8d6972f`](https://github.com/therealahall/recommendinator/commit/8d6972f4c4b821c6a2edc4da3ba2bcbf3e233878))

- **settings**: Pin tracked permissions and deny the execution escapes
  ([`027a397`](https://github.com/therealahall/recommendinator/commit/027a397e637057e4f508c1afff0841ed1002d1fb))

### Continuous Integration

- Check the repository root conftest.py alongside src and tests
  ([`9988a1a`](https://github.com/therealahall/recommendinator/commit/9988a1a88e688dc6a09a1d7b6d8e9d6015a28a48))

### Documentation

- Describe who owns rating, review, status and the rest
  ([`7ec5d67`](https://github.com/therealahall/recommendinator/commit/7ec5d6742b5584a4374795d8a24e6b66813190e3))

- **contributing**: Document the root conftest and its fixtures
  ([`53bb3b7`](https://github.com/therealahall/recommendinator/commit/53bb3b7b2d922244093ebd87f6db6f8003f08f38))

- **workflow**: Freeze scope and stop the review loop at convergence
  ([`6dc52b0`](https://github.com/therealahall/recommendinator/commit/6dc52b0f00c139f3fc0f7793479e505c5bd9116f))

### Testing

- **conftest**: Give every collected tree the isolation fixtures
  ([`39c2ff8`](https://github.com/therealahall/recommendinator/commit/39c2ff8d3101657abbcc57f54c494bccc85fd7d5))

- **package-version**: Assert the dev tree layout instead of skipping
  ([`c75c194`](https://github.com/therealahall/recommendinator/commit/c75c1940202405735af975cec99260e9339bbb69))

- **parity**: Pin the bounds the interfaces have to share
  ([`af4dfeb`](https://github.com/therealahall/recommendinator/commit/af4dfeb4b3c3b9a08deac18bc865b67c8abd827a))

- **review-gate**: Pin the guidance, the grants and the marker syntax
  ([`96a0c42`](https://github.com/therealahall/recommendinator/commit/96a0c42fdada1b3fb539f1976e0d075da7aad16e))


## v0.24.0 (2026-07-29)

### Features

- **docker**: Make the published port's host interface configurable
  ([`f687a05`](https://github.com/therealahall/recommendinator/commit/f687a0590e132d11d1a32c70f16b9fcd1534636b))

- **frontend**: Let the dev server port and proxy target come from env
  ([`d849b20`](https://github.com/therealahall/recommendinator/commit/d849b20a58dc56a201f9ae05d5d1a00f29f4942d))


## v0.23.0 (2026-07-28)

### Bug Fixes

- **storage**: Tell the operator when a config.yaml secret is migrated or ignored
  ([`1edeced`](https://github.com/therealahall/recommendinator/commit/1edeced7949b426c32d4cc2cfd32bc73e1422a40))

- **web**: Guard every config.yaml leaf read before the database opens
  ([`a9cf485`](https://github.com/therealahall/recommendinator/commit/a9cf4855c9268c8b7f3cd3d624f4d5b138e4dd16))

- **web-ui**: Keep focus and convey state across the settings surface
  ([`ed4c283`](https://github.com/therealahall/recommendinator/commit/ed4c283185b5f8ea7e53833ceea320860d501fa6))

### Chores

- **agents**: Drop the project copies of the shared review agents
  ([`35679dd`](https://github.com/therealahall/recommendinator/commit/35679dd4ece54374d2abe517efcb5daa2679bfa9))

- **config**: Trim example.yaml to bootstrap only
  ([`49b68b9`](https://github.com/therealahall/recommendinator/commit/49b68b974297fae4fefd865559a065a5c5343eab))

- **config**: Trim example.yaml to what stands the app up
  ([`8702706`](https://github.com/therealahall/recommendinator/commit/8702706f03a452078088bb1529786d2aa5e689ec))

### Documentation

- Describe settings as database backed rather than file backed
  ([`76cdd74`](https://github.com/therealahall/recommendinator/commit/76cdd74b15f48dc96753cca8ad3f17dcaf1a6bb8))

- Document database-backed global config precedence
  ([`3a0a4d5`](https://github.com/therealahall/recommendinator/commit/3a0a4d54f3c7d53cc1b125a8241c5766a349a659))

- **claude**: Drop stale cavecrew-investigator references
  ([`61e6b3b`](https://github.com/therealahall/recommendinator/commit/61e6b3b6f206d73d8b81acf95fed32f2decbe796))

- **claude**: Note the settings module in project structure
  ([`21dd100`](https://github.com/therealahall/recommendinator/commit/21dd1002e511312d283d1b4e3fb8443b1da61298))

- **patterns**: Document the frontend and Vue review rules
  ([`edeef5d`](https://github.com/therealahall/recommendinator/commit/edeef5dc72cf8ad17e397015b98b9b3e771bcb06))

- **settings**: Document the database-backed settings layer
  ([`9f51dca`](https://github.com/therealahall/recommendinator/commit/9f51dcac565d6b74bf18ad994dd6cfd198a43497))

### Features

- **cli**: Add settings command group
  ([`12b68dc`](https://github.com/therealahall/recommendinator/commit/12b68dca41a27bdc1b4b5ba18cf95c887bf25857))

- **cli**: Give the settings group --format json on every command
  ([`340cb14`](https://github.com/therealahall/recommendinator/commit/340cb142dfe5cbf4e5850d056ac070234926e001))

- **config**: Layer const < yaml < db and drop seed-on-boot
  ([`8a8d67f`](https://github.com/therealahall/recommendinator/commit/8a8d67f77f186e684ad7f54608312db5413e4c95))

- **enrichment**: Read provider api keys from the encrypted store
  ([`9266c98`](https://github.com/therealahall/recommendinator/commit/9266c98a72b3f6d44c1bea038207b32d0195234b))

- **settings**: Add config metadata registry and shared service
  ([`8bba048`](https://github.com/therealahall/recommendinator/commit/8bba048458158a6805529986c9e3c5da43f316f6))

- **settings**: Port the TMDB and conversation leaves into the registry
  ([`581c7b5`](https://github.com/therealahall/recommendinator/commit/581c7b5ecbfb6d505941d584f4ed36e0cf2c710f))

- **storage**: Encrypt global secrets and clean up seeded settings
  ([`42ebbe2`](https://github.com/therealahall/recommendinator/commit/42ebbe299df814dd0fb88319ced1095b01b6cc55))

- **storage**: Persist global config in a database-backed settings store
  ([`603ae69`](https://github.com/therealahall/recommendinator/commit/603ae693a8b0fa7033852b8f627c0039b96d8253))

- **web**: Add settings api endpoints
  ([`c3f9883`](https://github.com/therealahall/recommendinator/commit/c3f988375cc232dfc802a51262242881b4215f47))

- **web**: Add the settings page
  ([`bc37e05`](https://github.com/therealahall/recommendinator/commit/bc37e05ed758ad5d486ce236c769645c75eb4d14))

- **web,cli**: Seed and overlay database config on startup
  ([`772d3a4`](https://github.com/therealahall/recommendinator/commit/772d3a45cd0a64806d94a38c308d9a7c4f88b8a4))

### Refactoring

- **ingestion**: Drop the unwired conflict resolution machinery
  ([`dd1caff`](https://github.com/therealahall/recommendinator/commit/dd1caff7afa474bc7a61ee5fd971c971b30974a9))

### Testing

- **config**: Cover config reload, secret sweep and log path containment
  ([`18ccbe1`](https://github.com/therealahall/recommendinator/commit/18ccbe1fbc40704c3f78114423881d8a5ba4657a))

- **recommendations**: Verify global defaults act as new-user fallback
  ([`69bd959`](https://github.com/therealahall/recommendinator/commit/69bd9593039d85d77b9f8b05663ecd8815edef92))


## v0.22.0 (2026-07-27)

### Bug Fixes

- **ingestion/calibre_web**: Stop importing Calibre star ratings
  ([`b2fd476`](https://github.com/therealahall/recommendinator/commit/b2fd476e125fa822829de75da14dc332414a9ec1))

- **sync**: Sync database-only sources from the per-source path
  ([`7463774`](https://github.com/therealahall/recommendinator/commit/7463774358de20eed296cb93bf3884ebd5d665ae))

### Chores

- **config**: Add Calibre-Web example configuration
  ([`5c28e1e`](https://github.com/therealahall/recommendinator/commit/5c28e1e7d68ad55f3cb7020561ff5eb5ca5d6d14))

### Documentation

- Add Calibre-Web to source documentation
  ([`1989c13`](https://github.com/therealahall/recommendinator/commit/1989c139e5c8e3d35c3b92a81b839b9f937ae692))

- Document setting source secrets in the add-source modal
  ([`74ec592`](https://github.com/therealahall/recommendinator/commit/74ec59203fb9296ee0b3e41328bfe290c35b07c4))

- **ingestion/calibre_web**: Note ratings are not imported
  ([`73c26cb`](https://github.com/therealahall/recommendinator/commit/73c26cbf73c2e8302a3c9715e157e825a96984bb))

### Features

- **ingestion**: Add Calibre-Web book source plugin
  ([`1190373`](https://github.com/therealahall/recommendinator/commit/1190373e8cb04218f46ac98f1a807f47e3dd45b0))

- **web**: Allow hyphens in source ids
  ([`526d875`](https://github.com/therealahall/recommendinator/commit/526d875147a610dd11199dc1d58d6b1e97c2f4e0))

- **web**: Set source secrets when creating a source in the web UI
  ([`0e46c44`](https://github.com/therealahall/recommendinator/commit/0e46c447d5e2a946ec805ef0d38cd0c78b88e5fe))

### Testing

- **enrichment**: Make the already-running guard test deterministic
  ([`43b5f38`](https://github.com/therealahall/recommendinator/commit/43b5f38349f3e4db6bd1f7f1a27a9148aee3c1c8))

- **ingestion/calibre_web**: Cover protocol-relative and userinfo next links
  ([`4fe51cc`](https://github.com/therealahall/recommendinator/commit/4fe51cc63b7f81b33dcb355a17e06d0fa9ebabbe))

- **ingestion/calibre_web**: Cover read/unread sync without ratings
  ([`ea9eac2`](https://github.com/therealahall/recommendinator/commit/ea9eac2743ea889888da66a12371169cd7887da6))

- **sync**: Cover per-source sync of database-only sources
  ([`93e37f1`](https://github.com/therealahall/recommendinator/commit/93e37f166b38ec1954c17429bb47a3b12b81ac69))

- **web**: Cover sync-trigger error reset and polling lifecycle
  ([`c3e07d6`](https://github.com/therealahall/recommendinator/commit/c3e07d6106c7305f04ad0947087fe1d3943a622b))


## v0.21.0 (2026-07-23)

### Chores

- **agents**: Forbid ephemeral verification in review agents
  ([`46cc2d5`](https://github.com/therealahall/recommendinator/commit/46cc2d519e9bcd073440a8e6dfdb07401c827ad8))

### Features

- **ingestion**: Split goodreads into goodreads_csv, add goodreads_rss
  ([`b29d095`](https://github.com/therealahall/recommendinator/commit/b29d095321ad1b72eb24565971dc7b5d5e2073e4))

### Breaking Changes

- **ingestion**: Config that sets `plugin: goodreads` must change to `plugin: goodreads_csv`. Stored
  data relabels itself on first startup, but you have to update the config file by hand.


## v0.20.1 (2026-07-23)

### Bug Fixes

- **recommendations**: Fall back to latest season date for completed TV shows missing date_completed
  ([`732017e`](https://github.com/therealahall/recommendinator/commit/732017e949ef8299b023b33edd2f76fbee685641))


## v0.20.0 (2026-07-14)

### Chores

- **config**: Add storygraph_csv example input block
  ([`0446cc7`](https://github.com/therealahall/recommendinator/commit/0446cc71f7c99c9e90fde067df23ca0b61a16d87))

### Documentation

- **sources**: List The StoryGraph as a book source
  ([`a0e26f7`](https://github.com/therealahall/recommendinator/commit/a0e26f7f3c5e5da33f91da4f14d3aee6000f8f35))

### Features

- **ingestion**: Add StoryGraph CSV source plugin
  ([`35e61d0`](https://github.com/therealahall/recommendinator/commit/35e61d07433c4730aea20695a73c0f0df46c5927))


## v0.19.1 (2026-07-08)

### Bug Fixes

- **conversation**: Exclude ignored and unrated items from chat signal
  ([`62c6239`](https://github.com/therealahall/recommendinator/commit/62c62395ca7c89c22bfd3ec0ea1375c0623dce55))

- **recommendations**: Route engine and similarity through the signal set
  ([`153ab50`](https://github.com/therealahall/recommendinator/commit/153ab5032ed81ec4c160d979b10ae2631f839e85))

- **storage**: Add signal-set accessor for taste-shaping items
  ([`1a2c132`](https://github.com/therealahall/recommendinator/commit/1a2c132b00c7bc9b644751e0a74d5c857465fd09))

- **web**: Use the signal set for streaming recommendation blurbs
  ([`71fcee4`](https://github.com/therealahall/recommendinator/commit/71fcee4701d313d553d504461fb4206d5a7a0caf))

### Documentation

- Explain the taste-signal set and ignored-item handling
  ([`f9757a8`](https://github.com/therealahall/recommendinator/commit/f9757a8bfe351a3d3a3548bb8e63252251def130))


## v0.19.0 (2026-07-02)

### Bug Fixes

- **storage**: Merge season watch dates by recency, keep manual edits
  ([`26a661c`](https://github.com/therealahall/recommendinator/commit/26a661c43d9f06184162c905d679049eef70186c))

- **storage**: Remove one-time seasons_watched_dates backfill
  ([`d7e5a05`](https://github.com/therealahall/recommendinator/commit/d7e5a05f151199d380dd280f53a68079f9a454e3))

- **utils**: Add later-date helpers for season watch dates
  ([`6eaefed`](https://github.com/therealahall/recommendinator/commit/6eaefed6a0923ad3e3db9526d3b30bc9bc2c7bf3))

### Documentation

- Document TV season completions and new clusters
  ([`c5edb6e`](https://github.com/therealahall/recommendinator/commit/c5edb6e3671f9a339288bd203aa4e610e410f43e))

- Update season-date merge rule and drop backfill mentions
  ([`d6883ba`](https://github.com/therealahall/recommendinator/commit/d6883bae886e2c6bce08713f3d672b17e533658c))

### Features

- **ingestion**: Record per-season watched dates from Trakt
  ([`1247309`](https://github.com/therealahall/recommendinator/commit/1247309f8eea9a89a4c9ca40ed22ed5bd6118af3))

- **recommendations**: Add six new genre clusters
  ([`8c68adc`](https://github.com/therealahall/recommendinator/commit/8c68adce22c64eac983a5ee49f4e4988542e8cc1))

- **recommendations**: Count finished seasons in variety ladder
  ([`72a3c3c`](https://github.com/therealahall/recommendinator/commit/72a3c3c3e969dc520706b83e71f938ee5080f4e0))

- **storage**: Seed season watched dates on upgrade
  ([`178d2aa`](https://github.com/therealahall/recommendinator/commit/178d2aadda31b39bbef6ac593c7ddea389473b69))

- **storage**: Stamp per-season watched dates on manual edits
  ([`6d16197`](https://github.com/therealahall/recommendinator/commit/6d1619781d16646725a6d29d7f860f8ea4d5cf53))

- **utils**: Add latest_season_watched_date helper
  ([`3bcd2bd`](https://github.com/therealahall/recommendinator/commit/3bcd2bd542cff5a72f40034520370e27fb0f2545))

- **utils**: Add parse_iso_timestamp helper
  ([`dd786c6`](https://github.com/therealahall/recommendinator/commit/dd786c614dfa3e8bfc0c26d6bb157dded1b56954))


## v0.18.0 (2026-07-01)

### Bug Fixes

- **ingestion**: Only mark a Trakt season watched once you finish it
  ([`b0cc72c`](https://github.com/therealahall/recommendinator/commit/b0cc72cb7b0a618954eaba39e48f357b68cf951a))

- **ui**: Keep screen reader labels out of copied text
  ([`8511f0e`](https://github.com/therealahall/recommendinator/commit/8511f0e072fee16e645be02f87886a3d8508e884))

### Chores

- **config**: Add Trakt example configuration
  ([`2edd4e1`](https://github.com/therealahall/recommendinator/commit/2edd4e19e86b0d501bc7dcb816fbb37ba0549801))

### Documentation

- Add Trakt to source documentation
  ([`afd7d59`](https://github.com/therealahall/recommendinator/commit/afd7d59fc1091f7242a3aad2e8e7646b978411ad))

- **ingestion**: Explain Trakt fully watched season tracking
  ([`9209ca6`](https://github.com/therealahall/recommendinator/commit/9209ca699a988ed72dcebd0ffdea248c8b9ff9fc))

### Features

- **auth**: Add Trakt OAuth device-code authentication
  ([`081cc7f`](https://github.com/therealahall/recommendinator/commit/081cc7f379ed629173bf3cf5184e729e8f037fdf))

- **ingestion**: Add Trakt TV and movie source plugin
  ([`d9d924d`](https://github.com/therealahall/recommendinator/commit/d9d924d39b0203385f87ccc9d51190e429e91a26))

- **ui**: Add Trakt device-code connection flow
  ([`e81c5da`](https://github.com/therealahall/recommendinator/commit/e81c5dadaaf820822d70cb2da81d63e9366ff337))


## v0.17.0 (2026-06-28)

### Documentation

- **cli**: Document the --needs-rating flag
  ([`f9fd3ca`](https://github.com/therealahall/recommendinator/commit/f9fd3caa2247884f2268f4b53342fbded6ecca8e))

### Features

- **cli**: Add --needs-rating flag to library list
  ([`eecdf01`](https://github.com/therealahall/recommendinator/commit/eecdf019327af30ecd8be71ff2889022727d8d68))

- **storage**: Add unrated_only filter for items with no rating
  ([`eddf176`](https://github.com/therealahall/recommendinator/commit/eddf1767cc25e8cef6108c71637c4c616d0d1149))

- **ui**: Add Needs rating toggle to the library view
  ([`537dc6c`](https://github.com/therealahall/recommendinator/commit/537dc6c4f8d6541b7fbb9e8f1a5be96644e9be1b))

- **web**: Add needs_rating param to the items endpoint
  ([`dc86002`](https://github.com/therealahall/recommendinator/commit/dc86002ec766588b3b12adfd322d5ebdf4cc8e1d))


## v0.16.1 (2026-06-28)

### Bug Fixes

- **enrichment**: Redact API keys from enrichment status and logs
  ([`41ebf84`](https://github.com/therealahall/recommendinator/commit/41ebf841eaa6b08c0c18973d1e6b5bd72853e56d))

- **enrichment**: Redact the RAWG API key from error messages
  ([`990e5a3`](https://github.com/therealahall/recommendinator/commit/990e5a3f94451da4a16326d244f43064c3286921))

- **enrichment**: Redact the TMDB API key from error messages
  ([`db3b6a6`](https://github.com/therealahall/recommendinator/commit/db3b6a60eac5bf8b9df6581bcd0fcd13c5a183ac))

- **utils**: Add helper to scrub secrets from request errors
  ([`2864625`](https://github.com/therealahall/recommendinator/commit/28646258c989beaa16f42de24c7794b3427ab79e))

### Documentation

- Tell plugin authors to scrub request errors
  ([`4f4a3f6`](https://github.com/therealahall/recommendinator/commit/4f4a3f68081f0db0a5235b7674418bfb817b0220))

### Refactoring

- **steam**: Use the shared request error scrubber
  ([`fccd5f9`](https://github.com/therealahall/recommendinator/commit/fccd5f9a4c91b860c61d87dbb550212c2c49443f))


## v0.16.0 (2026-06-28)

### Documentation

- Document the library search
  ([`41f6f91`](https://github.com/therealahall/recommendinator/commit/41f6f91dc085cbc7fe6bcca5f4b13ca1aab29e7f))

### Features

- **cli**: Add a search option to library list
  ([`078f23c`](https://github.com/therealahall/recommendinator/commit/078f23cb0a5e34031093b04072e44b97f1db86c4))

- **enrichment**: Populate movie director from TMDB
  ([`e45333b`](https://github.com/therealahall/recommendinator/commit/e45333be64d1c7e6260d8cefb3d9ce04461ba023))

- **storage**: Add fuzzy title and creator search
  ([`d5ec1a3`](https://github.com/therealahall/recommendinator/commit/d5ec1a36c7e96e6c976b9f5230c0f0d72b18b0b2))

- **storage**: Filter library items by title or creator
  ([`062d022`](https://github.com/therealahall/recommendinator/commit/062d02215ef771a05c1affb1c5f24da90ad865a0))

- **web**: Add a search box to the library view
  ([`2ba6f66`](https://github.com/therealahall/recommendinator/commit/2ba6f666e5d928fb4aa25205ddf53f144e59cc7a))

- **web**: Add a search parameter to the items endpoint
  ([`1b8879d`](https://github.com/therealahall/recommendinator/commit/1b8879d6d5284182d64e06cdcda672b0c3197e6c))


## v0.15.0 (2026-06-28)

### Bug Fixes

- **web**: Lay the library card metadata out in two rows
  ([`633143a`](https://github.com/therealahall/recommendinator/commit/633143a03b4ec94b9828017034c0d343f427d60a))

- **web**: Live-update enrichment progress on the data view after sync
  ([`7ddc775`](https://github.com/therealahall/recommendinator/commit/7ddc7758074ecc176d4d50021a8fc68d07f8f34a))

### Documentation

- Align library card wording and theme tokens with the redesign
  ([`b5fdd76`](https://github.com/therealahall/recommendinator/commit/b5fdd766e87116f5abf007519fcd8b2d12690094))

- Document surfacing and manually editing enrichment metadata
  ([`c0419a7`](https://github.com/therealahall/recommendinator/commit/c0419a7a6d480aaf4af8415fd606521cfd6e6594))

- Refer to the enrichment and ignored markers as badges
  ([`d7b5f90`](https://github.com/therealahall/recommendinator/commit/d7b5f90b1bae2eb1e73ed109db75637137669dea))

### Features

- **cli**: Add enrichment filter and manual metadata editing to library commands
  ([`0eeb977`](https://github.com/therealahall/recommendinator/commit/0eeb9777b57816f828e551dd9f7768cc0b07a148))

- **enrichment**: Filter the library by enrichment state and persist manual metadata
  ([`6bec7d8`](https://github.com/therealahall/recommendinator/commit/6bec7d8e3af90a5d85a56b4ba0eb479130d90e51))

- **web**: Redesign library card metadata into an anchored meta bar
  ([`a16c03e`](https://github.com/therealahall/recommendinator/commit/a16c03ec9ce1d1fbfe5ae846802e306c81c644f8))

- **web**: Surface enrichment state and manual metadata editing in the library
  ([`8c57187`](https://github.com/therealahall/recommendinator/commit/8c57187c93b0832b3f2566b5a8edaa57fa1131d7))

### Testing

- **web**: Add ContentItemResponse fields to recommendation fixtures
  ([`35ab423`](https://github.com/therealahall/recommendinator/commit/35ab4236a1d87ff0525a3ed558bd2b48b9880bbf))

- **web**: Cover the redesigned library card metadata and rating
  ([`fd8dcdd`](https://github.com/therealahall/recommendinator/commit/fd8dcddcc70d3aa8438e3ab33abe3e5fed96af19))

- **web**: Cover the two-row library card metadata layout
  ([`1d1ee10`](https://github.com/therealahall/recommendinator/commit/1d1ee10d519cf7404c0f14a2d833c155dc65684d))


## v0.14.0 (2026-06-27)

### Bug Fixes

- **recommendations**: Make TV show recommendations actionable
  ([`42133f4`](https://github.com/therealahall/recommendinator/commit/42133f4ac5077fb299f9633f289b8055bce4392e))

### Documentation

- **recommendations**: Describe the recommendation card actions
  ([`a2f1989`](https://github.com/therealahall/recommendinator/commit/a2f1989c73608cf89c0f15c1a46e075240dac9eb))

### Features

- **recommendations**: Add mark complete action with edit modal
  ([`e0497b3`](https://github.com/therealahall/recommendinator/commit/e0497b3ec4743531ae88a4cfa5a728d17c4c041d))

### Refactoring

- **web**: Remove the legacy static web UI
  ([`67e5831`](https://github.com/therealahall/recommendinator/commit/67e5831945fb2d9cf4afabf8e2d54a0993a045e4))

### Testing

- **recommendations**: Cover the mark complete action
  ([`91e227e`](https://github.com/therealahall/recommendinator/commit/91e227e9f60d25d538b0470e5c2d85cd5a478e0c))

- **recommendations**: Cover TV show db_id handling
  ([`e65e317`](https://github.com/therealahall/recommendinator/commit/e65e317437bb71ef6f9ea8da265a5059aa2ce1ef))


## v0.13.0 (2026-06-26)

### Bug Fixes

- **a11y**: Keep slider tooltip text in the accessibility tree
  ([`61a53e8`](https://github.com/therealahall/recommendinator/commit/61a53e80a41a3dc0f731cffd9d882d4ffbf24eaf))

### Code Style

- **preferences**: Drop the divider above the variety slider
  ([`98cc7f6`](https://github.com/therealahall/recommendinator/commit/98cc7f692846d920fff31948721ff3951c5d517d))

### Documentation

- Describe the 0-5 variety penalty scale and Scoring/Rules layout
  ([`93325cb`](https://github.com/therealahall/recommendinator/commit/93325cb0b372bd8a850bbddbacedc6d49c313223))

- Describe the variety penalty slider
  ([`f13d245`](https://github.com/therealahall/recommendinator/commit/f13d245068ee7f07ca16a4a9980ea088babcedcf))

### Features

- **preferences**: Make variety after completion a strength slider
  ([`7d9a907`](https://github.com/therealahall/recommendinator/commit/7d9a9078d63b0a36479067e84a57136bbdcf5f93))

- **preferences**: Put variety penalty on the 0-5 scorer scale
  ([`5410a75`](https://github.com/therealahall/recommendinator/commit/5410a75d7bf3ff5bb5f5351a22617431cf99b3ca))


## v0.12.3 (2026-06-26)

### Bug Fixes

- **sorting**: Require word boundaries when matching similar titles
  ([`5cf3113`](https://github.com/therealahall/recommendinator/commit/5cf3113402c3b43a1c52fac308815a2b13a3871c))


## v0.12.2 (2026-06-26)

### Bug Fixes

- **sorting**: Only strip English articles when sorting titles
  ([`056286d`](https://github.com/therealahall/recommendinator/commit/056286de68dd721859fa4191473133b353078fba))

### Documentation

- Add OAuth setup guides to GOG and Epic plugin READMEs
  ([`985d95f`](https://github.com/therealahall/recommendinator/commit/985d95f41bfdfcb67411f0cf129bfa722b2228c3))

- Slim README into a landing page with per-topic guides
  ([`11edc7e`](https://github.com/therealahall/recommendinator/commit/11edc7e9356d6f828677acf238f82d2c2f5958eb))


## v0.12.1 (2026-06-05)

### Bug Fixes

- **recommendations**: Order half-numbered series entries by fractional position
  ([`1c31dfd`](https://github.com/therealahall/recommendinator/commit/1c31dfd7df0f1e2f57bc5fc6911713a313db31bf))

- **recommendations**: Soften variety penalty for the next book in an active series
  ([`87d8509`](https://github.com/therealahall/recommendinator/commit/87d8509967dca55b697a746147b66d0a2381a32b))

- **series**: Bound TV season numbers to prevent resource exhaustion
  ([`70dff1b`](https://github.com/therealahall/recommendinator/commit/70dff1b326aa6c6750137cb37d9c0542e3b1d724))


## v0.12.0 (2026-06-05)

### Chores

- **claude**: Restrict subagent tool access via frontmatter
  ([`21f5be8`](https://github.com/therealahall/recommendinator/commit/21f5be8ab2801f715597cf797fc5b260341195cd))

### Documentation

- Document the variety-after-completion penalty
  ([`9dd825e`](https://github.com/therealahall/recommendinator/commit/9dd825e10b8c5cce595fc5e211f194f017c13f2c))

- Move development patterns out of CLAUDE.md
  ([`ccf6e94`](https://github.com/therealahall/recommendinator/commit/ccf6e9447080f53e429df17e7036e51f4cd1e379))

### Features

- **cli**: Add preferences set-toggle and surface variety penalty
  ([`ab5b6a8`](https://github.com/therealahall/recommendinator/commit/ab5b6a8644a8c4f33bc9256e2a6a2bbc0d90566f))

- **recommendations**: Add stepped genre-fatigue variety penalty module
  ([`9e7cd43`](https://github.com/therealahall/recommendinator/commit/9e7cd435fa1297553f563f81296456561e220817))

- **recommendations**: Apply variety penalty after ranking (closes #74)
  ([`6e6a526`](https://github.com/therealahall/recommendinator/commit/6e6a526c1d9b28c28f899a50662182a5a11d48d5))

- **ui**: Show variety penalty in the score details panel
  ([`55d09b4`](https://github.com/therealahall/recommendinator/commit/55d09b42058bbca780c55e68b3d1d7be7c8c274c))

- **web**: Expose variety_penalty on recommendation responses
  ([`ad4c68c`](https://github.com/therealahall/recommendinator/commit/ad4c68c459f39c810636f6f9490f390b3e219754))


## v0.11.3 (2026-05-07)

### Bug Fixes

- **ui**: Keep stepper inline with content-type dropdown on mobile
  ([#58](https://github.com/therealahall/recommendinator/pull/58),
  [`1bb5bfb`](https://github.com/therealahall/recommendinator/commit/1bb5bfba74402fa46a439ddd81ba36648e1d59eb))


## v0.11.2 (2026-05-07)

### Bug Fixes

- **web**: Refresh enrichment stats on every poll tick while job running
  ([`282f88e`](https://github.com/therealahall/recommendinator/commit/282f88efc574cb584e824a950e6dbc36ab71bbd6))


## v0.11.1 (2026-05-07)

### Bug Fixes

- **core**: Resolve runtime version from pyproject.toml in source layouts (fixes #68)
  ([`ff9cd09`](https://github.com/therealahall/recommendinator/commit/ff9cd098b2c5531671c64383c96d5cec308e1151))

- **docker**: Bind-mount pyproject.toml in dev compose so version updates are live
  ([`2501dce`](https://github.com/therealahall/recommendinator/commit/2501dcea9aa79e8280e70e089184687450c6f2f1))

### Documentation

- Document pyproject-first version resolution and dev pyproject mount
  ([`8e5d5aa`](https://github.com/therealahall/recommendinator/commit/8e5d5aafb0f3e5facc3a3bd0e1b0b21442b744d5))

### Testing

- **core**: Cover pyproject-first version resolution and edge cases
  ([`3e37e89`](https://github.com/therealahall/recommendinator/commit/3e37e89c6b03c4eaf830ff26e06ea4cebbb5dd19))


## v0.11.0 (2026-05-05)

### Bug Fixes

- **sync**: Scrub exception text from client errors and emit 1-based progress
  ([`02354e7`](https://github.com/therealahall/recommendinator/commit/02354e7e4f2f514f06902ea1267e2c90f1c8ce93))

### Documentation

- **sync**: Document parallel sync, thread safety, and worker config (closes #45)
  ([`fa4fd10`](https://github.com/therealahall/recommendinator/commit/fa4fd10c82833572e5778e9d302e3f5ba82aaa6f))

### Features

- **cli**: --workers flag for parallel sync
  ([`d5f3430`](https://github.com/therealahall/recommendinator/commit/d5f34308f35e9c545c6aad2b456c449b2e262ef3))

- **storage**: Thread-safe writes for parallel multi-source sync
  ([`624e780`](https://github.com/therealahall/recommendinator/commit/624e780bd434d953e1e464be63215fa25fcb1b5c))

- **sync**: Parallel multi-source sync with ThreadPoolExecutor
  ([`84cdedf`](https://github.com/therealahall/recommendinator/commit/84cdedf7155a0cc156dad7f1487887f26fb7349e))

- **web**: Per-source progress + max_workers override on /api/update
  ([`ed58535`](https://github.com/therealahall/recommendinator/commit/ed585352653c7c654b8939f440771dc94997cf61))

- **web-ui**: Per-accordion progress and concurrent sync triggers
  ([`aee3843`](https://github.com/therealahall/recommendinator/commit/aee3843c0f367832dc5136947eae076590381e23))

### Refactoring

- **web**: Concurrent multi-job sync via SyncManager dict
  ([`441bbb8`](https://github.com/therealahall/recommendinator/commit/441bbb8078f23f986cdb0ca90e812db4c383900a))


## v0.10.0 (2026-05-03)

### Chores

- **sources**: Final review round-trip fixups
  ([`42d2c50`](https://github.com/therealahall/recommendinator/commit/42d2c5085d0ad3a984b9f8dfa1bc636267892bc8))

- **tests**: Rename TestEnrichmentProgressRegression to singular form
  ([`4b36766`](https://github.com/therealahall/recommendinator/commit/4b367668eb5a13dd67cb02384138acd50e3d7236))

### Documentation

- **claude**: Require all six review agents to re-run on every round
  ([`9638e59`](https://github.com/therealahall/recommendinator/commit/9638e596d0eec3d84f67ddbd499a06b786f65ec0))

### Features

- **sources**: Add/remove source flows + visual & a11y polish (closes #40)
  ([`5100122`](https://github.com/therealahall/recommendinator/commit/5100122311ee092ffeea99c9153081cf14835d4e))

- **sources**: Bulk-update CLI parity + DRY shared fakes + review fixups
  ([`1caec86`](https://github.com/therealahall/recommendinator/commit/1caec866bb4920f0ff1e0c2375d6559241431697))

- **sources**: Per-source DB-backed configuration with accordion UI (closes #39)
  ([`632e37b`](https://github.com/therealahall/recommendinator/commit/632e37bb0e6773b4d68690b8ee446be79aac464a))

### Refactoring

- **plugins**: Adopt folder-per-plugin layout (closes #36)
  ([`cda6044`](https://github.com/therealahall/recommendinator/commit/cda604439e7dca7255a4c2e3b79ec33fdbc91c37))


## v0.9.5 (2026-05-03)

### Bug Fixes

- **enrichment**: Report total_items upfront instead of per batch (closes #60)
  ([`0d1ac50`](https://github.com/therealahall/recommendinator/commit/0d1ac50e6cddd7fe374ef8ea764d42349ca71dba))


## v0.9.4 (2026-05-03)

### Bug Fixes

- **ingestion**: Drop Steam Store appdetails pass during sync (closes #34)
  ([`9e13d61`](https://github.com/therealahall/recommendinator/commit/9e13d610ad9a57d4785a9eeffac0289a1d4fb6c4))

- **ingestion**: Scrub Steam API key from request error messages
  ([`46027fb`](https://github.com/therealahall/recommendinator/commit/46027fbf526fc484426453bc102f76ea53bf862f))


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
