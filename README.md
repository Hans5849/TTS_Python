# Text-to-Speech (TTS_Python)

**Draft dependency warning:** Speech_Common PR #1 has an explicitly documented missing installer-module upload. Do not deploy this integration until that PR is completed and validated.

Independent text-to-speech application with its own environment, service, configuration, job state, and data directories.

## Install Speech_Common first

Install `Hans5849/Speech_Common` version 0.1.0 before this repository. Then clone `TTS_Python` and run:

```bash
./install.sh
```

On Debian/Ubuntu install Python 3.11+ for TTS (3.10+ for STT), matching `python3-venv`, FFmpeg, and eSpeak NG for TTS. Program prerequisites are checked before activation. The shared administrator installs the pinned common wheel into this application's dedicated environment; neither application imports or depends on the other.

The installer offers **Upgrade / preserve settings** or **Fresh program / default settings**. Fresh means a newly built environment plus backed-up/default application settings, **not deletion of inbox/outbox contents, archived originals, credentials, or processing history**. Existing user-data paths remain unchanged. Fresh installation remains stopped for configuration review unless `--start` is supplied.

```bash
./install.sh --fresh
./install.sh --yes --user YOUR_USER --no-start
./install.sh --fresh --yes --start
./update.sh
```

Custom first-install paths use `--program-dir`, `--inbox`, and `--outbox`. The service account must be non-root. Normally it is the invoking sudo user. Program files default to `/opt/tts-python`; configuration is `/etc/tts-python/config.yaml`; the canonical unit is `tts-python.service`. Interactive first installation offers path selection.

Upgrades stage and validate a complete new environment before stopping the old service, switch an active release pointer, preserve active/stopped state, and restore previous generated files/pointer after failed activation. Old release environments are retained for rollback until explicit uninstall. Source checkout updates use fast-forward-only Git operations; no hard reset or deletion of user data is performed.

## Operation

The persistent worker processes text/Markdown files from `inbox/normal`, `inbox/private`, and `inbox/cloud`. Private files prohibit all cloud providers. Speech-ready text, generated audio, archived originals, and failed sources are retained independently of program code. Identical basenames from different directories receive distinct, source-derived output identities.

```bash
tts dashboard
tts status
tts doctor
tts gpu
tts convert document.txt --private
tts service start
tts service stop
tts jobs
tts retry --failed
```

A direct `tts convert` command holds the same worker lock as the daemon. Stop the daemon first or submit through its inbox; concurrent manual conversion is rejected rather than duplicating work. A normal foreground terminal does not inherit systemd-only credentials. `tts doctor` checks the invoking process without making a billable API request.

Current speech engines are local eSpeak and cloud OpenAI. Optional Ollama preprocessing is an external service; its GPU placement/model residency must be configured separately. CPU/cloud speech does not acquire an unrelated GPU lock. Common model-lifecycle helpers are available to future GPU-backed speech engines.

The mature standalone converter is now `legacy/text_to_speech_v3.py`. `Audioconversion_generic_v3.py` remains a deprecated launcher that executes that same legacy implementation, not the newer package. Existing legacy JSON configuration is preserved for compatibility; active service configuration is YAML. The legacy audio assembly/cache behavior remains covered by its separate regression tests.

## Configuration and naming migration

New shared/TTS/STT service configuration uses YAML. TTS can still read legacy TOML for migration. Legacy STT installer YAML with repeated path keys is read only by the migration reader; newly written YAML rejects duplicate/unknown settings instead of silently accepting mistakes.

The canonical Python package is `tts_python`, the command is `tts`, and the service is `tts-python.service`. Old command/import names are compatibility adapters. Legacy service names become aliases of the **same** new unit, not additional workers. Old source checkouts and existing inbox/outbox names need not be renamed.

The installer inventories old `/etc/audioconversion` or `/etc/audio-transcription` records as data, adopts only identifiable application files, and records protected paths. TTS key files are migrated into root-only shared storage without overwriting conflicting credentials. Old configurations and releases may remain as migration/rollback resources; removal lists exactly what it will delete.

The file `docs/LEGACY_SETUP.md` is an archived copy of the old instructions, not the current installation procedure.

## Removal: preview first

```bash
sudo ./uninstall.sh --dry-run
sudo ./uninstall.sh --apply
# Remove both applications, keeping shared infrastructure and credentials:
sudo uninstall-speech --app all --apply
```

Program environments, generated units/commands, and owned configuration/state/logs outside protected directories are removable. Inbox/outbox contents and all hidden files are always preserved. For TTS this also protects archive, failed-source, and processed-text directories. Uninstalling one app never removes another app's pinned dependencies or shared keys. Explicit shared-cache/infrastructure cleanup requires `--purge-shared`; credential deletion additionally requires `--purge-credentials`. Host packages, Git checkouts, general caches, and unrelated GPU services remain untouched.

Missing ownership evidence, unknown added files, protected-path overlap, unsafe symlinks/mounts, failed service stops, and live foreground workers block removal. There is no bypass flag. A preserved path inventory remains after removal to protect retained user data during later cleanup. Reinstallation after deleting history can reprocess preserved inbox files; review them before starting a reinstalled worker.

## Testing and deployment checks

For source development install `Speech_Common` in the same development environment first, then install this package. CI pins the common implementation revision for reproducibility, runs offline unit tests, and performs an install/fresh/remove smoke test on an ephemeral Ubuntu runner. No test sends private text or makes a billable cloud call.

Target-host checks are still required for real CUDA memory release, multiple physical GPUs, external Ollama/Plex/Folding workloads, systemd permissions, and WSL. In WSL without systemd, use foreground workers and owner-only credential files; do not install a Linux display driver into WSL.
