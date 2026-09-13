# V3 validation results

Version: 3.0.0. Validation performed September 11, 2026.

## Result

**49 automated regression tests passed.** No paid speech API calls were made.

## Test environment

- Python 3.13.5 on Linux.
- FFmpeg 7.1.5 and matching ffprobe; real decoding, WAV processing, loudnorm, and MP3 encoding.
- No live OpenAI SDK/service invocation. Speech responses and service errors were simulated.
- The actual tiktoken package/tokenizer was unavailable in this environment. Sample-file tests used the conservative UTF-8-byte fallback. The tokenizer-enabled code path was additionally exercised with a local test double, not actual o200k token data.
- WSL, Windows-native Python, and older FFmpeg versions were not executed here.

## Sample-file checks

All six supplied notes were checked in normal and chapter modes. Their non-whitespace character sequence was preserved exactly, in order, with no missing or duplicated source content. Prepared text equalled the original text after the documented leading-BOM/newline/outer-whitespace normalization. Epilogues and the source-note section were retained.

| Sample | Prepared characters | Detected sections | Normal-mode requests | Chapter-mode requests |
|---|---:|---:|---:|---:|
| ENEE620_Week2_Review_REBUILT.txt | 6785 | 11 | 8 | 11 |
| ENEE620_Week3_Preview_REBUILT.txt | 5090 | 8 | 5 | 9 |
| ENEE630_Week2_Review_REBUILT.txt | 6350 | 13 | 6 | 13 |
| ENEE630_Week3_Preview_REBUILT.txt | 5411 | 9 | 6 | 10 |
| ENEE641_Week2_Review_REBUILT.txt | 5560 | 13 | 6 | 13 |
| ENEE641_Week3_Preview_REBUILT.txt | 5518 | 11 | 6 | 12 |

**These counts use the conservative byte fallback.** With tiktoken installed, normal-mode requests will generally be fewer; these are not claimed as the exact counts on the user's machine.

All six samples were also run through the complete batch pipeline using synthetic WAV responses, creating and validating six separate MP3s in a temporary test directory. A second run skipped all six without requesting an API client. Synthetic test MP3s were discarded and are not included as purported narration.

## Covered regressions

- Mathematical signs and already-spoken equations remain unchanged.
- Word-safe subdivision, including the 7,597-character / 3,800-character boundary regression.
- Oversized unbroken tokens fail explicitly rather than being silently cut.
- Whole-token, case-sensitive pronunciation rules do not corrupt HTML or cascade replacements.
- Headings, explicit Markdown headings, fenced code, source notes, and epilogues.
- Character budgets and multibyte text under the conservative token budget.
- Configuration validation, CLI precedence, model/voice compatibility, and invalid encodings.
- Corrupt JSON/cache records, checksum failures, and repair by regeneration.
- Later cached chunks survive failure of an earlier request.
- Selective retries, no retries on bad credentials/invalid requests, and length-error subdivision recipes.
- Interrupted downloads do not replace existing valid cached audio.
- Known unknown-length streaming WAV sentinels are repaired; finite-length truncated WAVs are rejected.
- Output collisions, unmanaged-output protection, process locking, and moved-cache adoption.
- Failed final encoding leaves an existing output untouched.
- Whole-recording two-pass normalization, chapter slicing, MP3 encoding, output validation, and playlists.
- Obsolete previously generated chapter tracks are cleaned without untracked-file deletion.
- Completed-output skipping and offline cached-audio reassembly without an API client.
- A file-specific failure allows later files to complete; an account-level failure stops the remaining batch.
- Subprocess timeouts and credential redaction.

## Not validated

No claim is made that the service will speak every word correctly, that any particular pronunciation is ideal, or that the same voice will sound perfectly continuous across requests. File integrity is not semantic speech verification. The first live run should be listened to before processing a large batch.

## Reproduce

```bash
python -m unittest -v test_tts_v3.py
```

Set `TTS_SAMPLE_DIR` to the directory containing the six optional `*REBUILT.txt` files to include the user-sample tests. Those files are deliberately not included in this distributable package.
