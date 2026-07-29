/**
 * Upload cap for POST /api/import, mirrored from `MAX_UPLOAD_BYTES` in
 * `src/web/upload_limit.py` (which `src/web/api.py` only re-exports). The
 * server stays the authority — it streams the body and
 * answers 413 — but the modal needs the number too so it can refuse an
 * oversized file before the user pays for the upload. The two values are
 * pinned together by `test_cap_is_mirrored_in_the_frontend_constant` in
 * `tests/test_web_api.py`.
 */
export const MAX_UPLOAD_MB = 50
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
