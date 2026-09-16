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
and restarts the service without replacing configuration or runtime data.

## Development

Install an editable development environment and run both test suites:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
python -m unittest -q test_legacy.py
```

The older single-file converter remains as `audioconversion_legacy.py` while its mature
audio assembly and cache behavior is incorporated into the package incrementally. New
standalone and service development should use the package and `tts` command.
