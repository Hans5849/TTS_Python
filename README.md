# Audio Conversion

Audio Conversion turns text and Markdown documents into speech from one shared Python
package. It can be run manually on a Linux desktop or WSL, or continuously as a Linux
systemd service. The service watches policy-specific inbox folders and preserves a
speech-ready text file alongside each generated recording.

Runtime documents, recordings, logs, job data, configuration, and credentials live
outside this source repository. All runtime paths are configurable.

## Linux installation

Python 3.11 or newer and the `python3-venv` package are required. FFmpeg is recommended,
and `espeak-ng` is required when the local speech engine is enabled. On Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv ffmpeg espeak-ng
```

From the checked-out source directory, run:

```bash
chmod +x install.sh update.sh
./install.sh
```

When attached to a terminal, the installer asks for three absolute paths:

1. **Program path** — defaults to `/opt/TTS_Python`.
2. **Inbox path** — defaults to `/srv/tts/inbox`.
3. **Outbox path** — defaults to `/srv/tts/outbox`.

The installer creates a virtual environment, installs the `tts` command, writes
`/etc/audioconversion/config.toml`, creates the runtime directories, installs and starts
the systemd service, and enables it at boot. If automation is non-interactive, set
`TTS_PROGRAM_DIR`, `TTS_INBOX_DIR`, and `TTS_OUTBOX_DIR` before running the installer.

The three inbox policies are:

- `inbox/normal`: use configured provider preferences and fallbacks;
- `inbox/private`: prohibit every cloud LLM and cloud speech provider;
- `inbox/cloud`: allow configured cloud services.

API credentials are never written to the repository or main configuration. Put them in
a root-managed `/etc/audioconversion/secrets.env`, for example:

```dotenv
OPENAI_API_KEY=replace-with-the-real-key
```

Then protect and activate the file:

```bash
sudo chown root:root /etc/audioconversion/secrets.env
sudo chmod 600 /etc/audioconversion/secrets.env
sudo systemctl restart audioconversion.service
```

Edit `/etc/audioconversion/config.toml` to select real Ollama models, providers, voices,
and output format. The included `config/example.toml` documents the available structure.

## Status dashboard

Run the interactive SSH-friendly dashboard:

```bash
tts dashboard
```

It shows the program, inbox, outbox, processed and failed folders; systemd state;
processing/provider selections; and counts for inbox, active, completed, and failed
files. Its menu provides service status, recent application logs, NVIDIA GPU status,
failed-file retry, refresh, and safe reset of completed/failed history and logs.
Resetting history never deletes source documents or generated audio.

For scripts and monitoring, `tts status` prints the same snapshot without opening the
menu. Direct commands are also available:

```bash
tts logs
tts errors
tts retry JOB_ID
tts reset
tts service status
tts service start
tts service stop
tts service restart
```

## Manual conversion

The standalone CLI and service use the same processing pipeline and configuration:

```bash
tts convert lecture.txt
tts convert lecture1.txt lecture2.txt lecture3.txt
tts convert sensitive.txt --private
```

A private conversion filters cloud providers before content reaches provider code and
fails if a required local provider is unavailable. Speech-ready text is written under
the configured `processed` directory as `NAME.tts.txt`; audio is written to the outbox.

Other useful commands include:

```bash
tts queue
tts jobs
tts config show
tts config path
tts config validate
tts engines
tts voices
tts models
```

## Updates

From the installed checkout or a newer source checkout:

```bash
./update.sh
```

If the installed program directory is a Git checkout, the updater performs a fast-forward
pull. Otherwise it copies the newer local source. It then upgrades the installed package
and restores the service to its previous running/stopped state without replacing
configuration or runtime data.

## Development

Install an editable development environment and run both test suites:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
python -m unittest -q test_tts_v3.py
```

The preserved single-file converter remains available while its mature audio assembly
and cache behavior is incorporated into the package incrementally. New standalone and
service development should use the package and `tts` command.

## Unattended processing and recovery

The watcher does not use file age as proof that a network copy is complete. It samples
both file size and nanosecond modification time and requires the configured number of
unchanged observations, separated by `stability_interval_seconds`, before queueing a
file. The default is three observations. Policy inboxes remain `normal`, `private`, and
`cloud`.

SQLite records each source version as its canonical inbox path, size, and nanosecond
modification time. A completed unchanged version is not synthesized again after a
restart; a changed size or timestamp creates a new eligible version. At service startup,
interrupted `processing` records return to `queued`. Job states are `waiting`, `queued`,
`processing`, `retry_wait`, `completed`, `failed`, and `cancelled`.

`processing.max_retries` means retries **after** the initial attempt. With the default of
2, a transient failure receives at most three total attempts. Backoff starts at
`retry_initial_seconds`, doubles after each failure, and is capped by
`retry_max_seconds`. Authentication, malformed input/configuration, unsupported models,
and other deterministic failures are permanent. Connection, timeout, rate-limit,
server, and transient subprocess failures are retryable. A source enters the failed
folder only after a permanent failure or retry exhaustion.

Useful job operations are:

```bash
tts jobs
tts jobs failed
tts retry JOB_ID
tts retry /absolute/source/path
tts retry --failed
tts reset --completed
tts doctor
tts gpu
```

Retry commands safely return sources from the failed policy folder to their corresponding
inbox; users do not edit SQLite. Resetting completed history does not delete source or
output files.

## Model lifecycle and GPU selection

Model-backed local providers may implement the optional load/unload lifecycle. The
service automatically loads such a provider before synthesis, keeps it resident while
work is arriving, never unloads it during active synthesis, and unloads it after
`tts.local.idle_timeout_seconds`. Set the timeout to `0` to keep a model resident.
Lightweight providers such as `espeak-ng` require no model lifecycle.

Configure a GPU using a stable physical identity:

```toml
[tts.local]
gpu = "auto"                 # visible GPU with most free memory
# gpu = 1                    # physical NVIDIA index
# gpu = "GPU-xxxxxxxx-..."   # preferred: full NVIDIA UUID
idle_timeout_seconds = 600
```

A physical NVIDIA index or UUID is not necessarily the CUDA logical index. For example,
`CUDA_VISIBLE_DEVICES=GPU-B,0` maps physical GPU B to `cuda:0` and physical GPU 0 to
`cuda:1`. `tts gpu` reports physical index, UUID, PCI bus ID, visibility, and resolved
logical index. GPU discovery is only a required diagnostic for a configured GPU-backed
provider; CPU and cloud configurations continue to run without NVIDIA hardware.

`tts doctor` validates configuration and provider-specific prerequisites without making
a billable synthesis request or printing secrets. It checks `espeak-ng`, FFmpeg for
encoded output, OpenAI credential presence, and NVIDIA selection only when appropriate.

## Safe upgrades

`./update.sh` remembers whether the service was active, stops it cleanly, performs only a
fast-forward Git update when installed from Git, reinstalls inside the dedicated virtual
environment, refreshes the unit, and restores the prior running state. Its failure trap
also attempts to restore a service that was active before the update. Configuration,
secrets, database state, inboxes, processed text, output, archives, failed sources, and
logs are not removed or reset.

## Preserved converter and migration

`Audioconversion_generic_v3.py` remains available and unchanged as the compatibility
implementation until the package demonstrates feature and output parity. Its mature
normalization, chunking, retry classification, caching, WAV validation/repair, lossless
assembly, FFmpeg processing, chapters/tags, atomic replacement, locking, and reporting
remain the reference for incremental extraction. The detailed inventory and migration
sequence are in `docs/V3_AUDIT_AND_MIGRATION.md`.
