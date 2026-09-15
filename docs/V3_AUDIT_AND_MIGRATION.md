# V3 behavior audit and incremental migration

## What V3 currently does

`Audioconversion_generic_v3.py` is a mature single-file batch converter. It discovers or
accepts multiple UTF-8 text/Markdown documents, preserves wording, detects sections,
creates token- and character-bounded requests, synthesizes OpenAI WAV chunks, resumes
from a content-addressed cache, assembles and normalizes audio with FFmpeg, writes MP3
metadata/chapters, validates output, and records reports. It also includes dotenv and
JSON configuration, a dry-run/export plan, dependency diagnostics, retry/rate limiting,
parallel files, atomic writes, locking, and collision protection.

## Reusable behavior

The following proven V3 functions remain valuable and are deliberately left intact:

* conservative source normalization and content-preservation checks;
* heading/section detection and natural boundary chunking;
* pronunciation dictionaries and document metadata extraction;
* token budgets, adaptive request splitting, rate limiting, retry classification;
* validated resumable WAV caching, streaming-WAV repair, lossless assembly;
* FFmpeg loudness normalization, MP3 encoding, chapter creation, and validation;
* safe error redaction, atomic files, locks, output ownership, and reports.

V3 remains runnable during the migration and its offline regression suite remains the
compatibility safety net. The package does not duplicate this audio machinery yet;
future adapters should extract it behind `TTSEngine` in small, tested steps.

## Technical debt and limitations

* One roughly monolithic module combines CLI, configuration, discovery, planning,
  OpenAI transport, caching, audio operations, and orchestration.
* Runtime output defaults beside the script, which is convenient for V3 but unsuitable
  as a server data-separation policy.
* Configuration is V3-specific JSON and environment loading rather than one schema for
  standalone and server modes.
* The OpenAI speech API is embedded in orchestration and there is no LLM provider,
  local TTS interface, privacy policy boundary, durable server queue, or watcher.
* There is no installed command, package boundary, systemd unit, or service dashboard.

## Migration plan

1. **Completed here:** retain V3 unchanged; add package/configuration boundaries,
   deterministic normalization/chunk classification, LLM/TTS interfaces, explicit
   privacy-aware routing, speech-ready artifacts, SQLite job state, polling watcher,
   status/CLI, installers, and a hardened service template.
2. Extract V3's chunk planning into the package while keeping golden compatibility
   tests for request boundaries and preservation.
3. Implement a V3/OpenAI engine adapter using its retry, cache, validated WAV assembly,
   normalization, tagging, and chapter logic; keep the thin cloud adapter experimental
   until parity is demonstrated.
4. Add concrete cloud LLM providers only with redacted logging and explicit secret
   environment variables. Add more local engines behind the same interface.
5. Add cache records for speech-ready text and provider fingerprints, queue retry and
   cancellation, filesystem event support, richer health checks, and package upgrades.
6. After field testing both modes and migration documentation, deprecate the legacy
   entry point; do not remove it until audio/output parity is verified.

## Security boundary

Configuration defaults use per-user XDG-like directories. The server example explicitly
uses `/srv`, `/var/lib`, and `/var/log`, but every path is configurable. The repository
contains only source, examples, templates, tests, and documentation. Private jobs filter
cloud providers before any source or processed content reaches provider code, and fail
closed if no local provider is configured. Secrets are exclusively supplied by the
environment or the optional root-managed systemd environment file.
