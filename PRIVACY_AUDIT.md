# What was reviewed before publication

The supplied Brakko files were checked for common personal paths, user profile names, email addresses, private IPs, likely hardcoded secrets, tokens, user account details, and known unrelated prior-project names.

This package excludes the original unredacted `manifest.json` and the nine raw steps containing prior-project context. The original `provenance.json`, original `TRAIL.md`, and six original raw steps were separately inspected and included byte-for-byte unchanged. The sensitive portion is published in redacted, re-hashed form.

Redaction rules:
1. Replace unrelated previous meme run names and the prior-run history section.
2. Remove model-internal `reasoning`, `reasoning_details` and opaque reasoning signatures.
3. Replace failed-attempt prompt arrays with counts, keeping final completed response and model ID.
4. Keep relevant names, design features, final prompt, run chronology, and original commitments.
5. Never include keys or `.env`, as these were not needed for verification.

**Security warning:** A keyword audit cannot prove that arbitrary text is completely non-sensitive. Read the published README and JSON files before making the GitHub repository public. Generation IDs and unique run timestamps are kept intentionally as verification aids and are linkable to the original account by its owner/provider.

The original run's final PNG SHA-256 differs from the uploaded display copy. The ZIP never claims that the display copy is hash-identical to the generated original.
