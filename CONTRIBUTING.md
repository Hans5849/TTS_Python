# Contributing

Thanks for contributing to the text-to-speech utility.

## Set up a development environment

Python 3.10 or newer is required. FFmpeg and FFprobe must be available on
`PATH` for audio-pipeline tests.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip -r requirements-tts.txt
```

## Make and test changes

Keep changes focused and preserve the existing command-line behavior unless the
change intentionally updates it. Add regression coverage for fixes and new
behavior. The test suite uses simulated speech responses and does not make paid
API calls.

```bash
python -m compileall -q Audioconversion_generic_v3.py test_tts_v3.py
python -m unittest -v test_tts_v3.py
```

Before submitting a pull request, update the README for user-facing changes and
describe both the motivation and the verification performed.

## Protect sensitive data

Never commit or paste OpenAI API keys, private source notes, generated speech,
or cache manifests containing private material. Use synthetic or sanitized text
in tests and issue reports. The repository `.gitignore` excludes common local
credentials, virtual environments, generated audio, and TTS work directories,
but contributors remain responsible for reviewing every staged file.
