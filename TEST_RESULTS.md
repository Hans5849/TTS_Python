# Validation results

The automated tests use synthetic audio and do not make paid API calls.

Run the package architecture tests:

```bash
python -m pytest -q
```

Run the preserved audio-pipeline regression tests:

```bash
python -m unittest -q test_tts_v3.py
```

The optional sample-document checks run only when sanitized `*REBUILT.txt` fixtures are
available through `TTS_SAMPLE_DIR`. Private source documents and generated recordings
are deliberately not part of this repository.
