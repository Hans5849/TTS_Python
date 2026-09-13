# Text-to-Speech V3: setup, upgrade, and use

`Audioconversion_generic_v3.py` converts one or more `.txt` or `.md` files into
**separate MP3 recordings**. It is designed for lecture reviews, read-aheads,
spoken mathematics, and similar educational text. It is not a summarizer or a
mathematical fact checker.

The script preserves the source wording by default. It does not change playback
speed or send a `speed` parameter to the speech API. Adjust speed in your player.

## Upgrade an existing installation

Keep the V2 script and its `_audio_parts` folders. Put the V3 script alongside
your notes, then use the existing Python virtual environment:

```bash
cd ~/tts
source .venv/bin/activate
python -m pip install --upgrade openai tiktoken
python Audioconversion_generic_v3.py --doctor
```

Inspect all selected notes without calling the speech API:

```bash
python Audioconversion_generic_v3.py --dry-run --export-plan
```

Choose `all`, `1 2 3`, `1-3`, or another selection. The exported plan contains
exact request text, detected headings, and a text-change diff.

Run the conversion:

```bash
python Audioconversion_generic_v3.py
```

For a clean separation from existing V2 outputs:

```bash
python Audioconversion_generic_v3.py --output-dir ./audio_v3
```

**V2 audio is not imported into the V3 cache.** V3 changes the request boundaries,
narration instructions, and audio pipeline. The first V3 conversion therefore
uses the speech API again. It never deletes the V2 cache. An existing final MP3
not owned by V3 is protected: choose another output directory, or deliberately
use `--overwrite` to replace it.

Keep the same `--output-dir` and generation settings on later runs so V3 finds
the same completed outputs and cache.

## New setup: WSL with Debian/Ubuntu-style package management

All following shell commands run inside the Linux/WSL terminal unless identified
as Windows commands. Python 3.10 or newer is required. The script also runs in
other environments with Python, FFmpeg, and the dependencies installed; the
package-install commands below are specifically for apt-based systems.

```bash
sudo apt update
sudo apt install -y python3 python3-venv ffmpeg

mkdir -p ~/tts
cd ~/tts
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --upgrade openai tiktoken
```

Alternatively, install the Python packages from the bundled file:

```bash
python -m pip install -r requirements-tts.txt
```

Do not use system-wide `pip`, `sudo pip`, or `--break-system-packages`. An
`externally-managed-environment` error means the system interpreter is being
used instead of the intended virtual environment.

Verify:

```bash
which python
python -c "import openai; print(openai.__version__)"
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
```

`which python` should show a path inside `~/tts/.venv/bin/`.

### API key

An existing `OPENAI_API_KEY` environment variable continues to work. Do not put a
key in the Python script, pronunciation dictionary, or JSON settings file.

For a temporary Bash session, enter the key without putting the key itself in
shell history or displaying it:

```bash
read -rsp 'OpenAI API key: ' OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
```

Check presence without printing the key:

```bash
python -c "import os; print('API key configured' if os.getenv('OPENAI_API_KEY') else 'API key missing')"
```

For persistent Bash configuration, an optional restricted-permission key file
can be created as follows. This replaces an existing key file at this path.

```bash
mkdir -p ~/.config/openai
(
    umask 077
    read -rsp 'OpenAI API key: ' key
    printf '\n'
    printf 'export OPENAI_API_KEY=%q\n' "$key" > ~/.config/openai/tts-key.sh
)
source ~/.config/openai/tts-key.sh
```

Add this line to `~/.bashrc` with your preferred editor, then reopen the terminal:

```bash
[ -r "$HOME/.config/openai/tts-key.sh" ] && source "$HOME/.config/openai/tts-key.sh"
```

Keep this credential file private and out of repositories and shared folders.
Text submitted for synthesis is sent to the configured OpenAI API endpoint.

### Access the folder from Windows

From WSL:

```bash
cd ~/tts
explorer.exe .
```

Drag the script and notes into that File Explorer window. Finished MP3s can be
copied out the same way. This is access from Windows on the **same computer** as
the WSL instance; it does not expose the folder to a different PC over the LAN.

To browse manually, enter `\\wsl.localhost` in File Explorer, then choose the
installed distribution and navigate to its Linux home directory and `tts`.
Use `wsl -l -v` in Windows PowerShell to see distribution names. Keep `.venv` in
the Linux filesystem, rather than copying it between machines. Recreate it on a
new computer.

## Normal weekly workflow

Put the week's source files next to the script:

```text
tts/
    Audioconversion_generic_v3.py
    notes_a_review.txt
    notes_a_preview.txt
    notes_b_review.txt
    notes_b_preview.txt
    notes_c_review.txt
    notes_c_preview.txt
    .venv/
```

Start with the interactive file picker:

```bash
cd ~/tts
source .venv/bin/activate
python Audioconversion_generic_v3.py
```

Or bypass the picker:

```bash
python Audioconversion_generic_v3.py \
    notes_a_review.txt notes_a_preview.txt \
    notes_b_review.txt notes_b_preview.txt \
    notes_c_review.txt notes_c_preview.txt
```

Use a dedicated input folder to avoid selecting old or unrelated text files:

```bash
python Audioconversion_generic_v3.py --input-dir ./weekly_notes --all --output-dir ./audio
```

`--all` means all discovered `.txt`/`.md` files, not just three courses or the
current week. Default discovery checks the current directory and script
directory. `--input-dir` limits discovery to that directory. `--recursive` adds
subfolders; known cache folders, hidden folders, and virtual environments are
excluded. README and requirements files are excluded from automatic discovery.
Explicitly named files are not filtered this way.

Files are processed **sequentially**. They are not concatenated into one
cross-course recording, and this uses individual speech requests, not a separate
asynchronous Batch API job.

### Optional `tts` shortcut

Replace an old V2 alias in `~/.bashrc`, rather than keeping two definitions:

```bash
alias tts='cd ~/tts && .venv/bin/python Audioconversion_generic_v3.py'
```

Reload with `source ~/.bashrc`. Then `tts`, `tts --dry-run`, and
`tts --output-dir ./audio_v3` work without manual virtual-environment activation.

## What changed from V2

### Source preservation

Default preparation removes a leading Unicode BOM, standardizes line endings,
and trims whitespace at the beginning/end of the document. It does not remove
minus signs, plus signs, comparisons, code, citations, source notes, or epilogues.
There is no LLM rewriting step.

Uppercase standalone headings and common labels such as Summary, Epilogue, and
Source note are recognized structurally. Explicit Markdown headings are also
recognized. This detection is heuristic: use `--headings none` to disable it.
Detection does not remove the heading's words.

`--markdown-headings` explicitly removes only leading Markdown `#` heading
markers outside fenced code/math blocks. Other ambiguous syntax is preserved.
`--no-cleanup` is retained as a compatibility flag; preservation is already the
default.

Already-spoken equations stay as written. Ambiguous expressions such as “two
divided by j minus i plus one” are **not** silently reinterpreted or corrected.
A human should revise ambiguous source text before generating audio.

### Chunk planning and limits

Whole sections are packed together when they fit. Oversized sections are split
at paragraph, sentence, line, clause, or whitespace boundaries. The splitter
checks that the non-whitespace character sequence is preserved, in order. It
allows an extra request rather than cutting a word to achieve a balanced count.
A single unbroken token that cannot fit is rejected with an actionable error.

Default limits are 3,800 characters and a local budget of 1,850 tokens including
instructions and a 128-token reserve. Token estimates use `o200k_base` when
`tiktoken` is available. They are **not billing measurements** or a guarantee of
identical server tokenization. The service's length checks remain authoritative.

If tiktoken or its tokenizer data cannot load, `--token-counter auto` switches to
a conservative UTF-8-byte budget and prints a warning. This is intentionally
less efficient and can create substantially more requests. Install tiktoken for
the normal plan. Its first use may download tokenizer data; no speech API request
is made by that download. `--token-counter tiktoken` makes tokenizer availability
mandatory; `--token-counter bytes` deliberately selects the offline fallback.
Changing the counter may change chunk boundaries and require new synthesis.

A length rejection from the API causes safe subdivision, not repeated submission
of the same oversized input. The subdivision recipe is saved for later resumes.

### Narration and audio

The default model is the dated `gpt-4o-mini-tts-2025-12-15` snapshot and the default
voice is `cedar`. The instructions request a consistent educational delivery,
natural heading pauses, clear variables/initialisms, and no added material.
A pinned snapshot reduces unintended model changes; it does not guarantee
identical audio across separate generations.

Pipeline:

```text
Speech API WAV response
    -> validate/repair known streaming-WAV length sentinels
    -> canonical lossless 24 kHz mono PCM WAV cache
    -> assemble the complete document
    -> optional two-pass loudness normalization
    -> encode the final 128 kb/s MP3 once
    -> decode/probe validation
    -> atomic replacement of the final output
```

Normalization is enabled by default. The requested mono target is -19 LUFS with
a -1.5 dBTP pre-encoding ceiling and an LRA target of 11. These are configurable
starting values, not a claim of a universal listening standard. FFmpeg can use
dynamic processing when linear normalization cannot meet the targets. Lossy MP3
encoding can change measured peak values. Very short/silent recordings whose
loudness cannot be measured are copied without normalization, with a warning.

Use `--no-normalize` to disable this step. No speed changes, silence trimming,
noise reduction, music, or crossfades are applied.

An extra 0.4 seconds of silence is inserted at request boundaries that begin a
new section. Pauses **within** a request are controlled by the model; exact timing
is not promised. Set `--section-pause 0` to disable inserted silence.

### Reliability and resume

V3 saves each audio chunk under a hash of its exact request configuration and
text, independent of its numbered playback position. Separate metadata records
include audio checksums. An interrupted earlier chunk cannot erase later valid
cache entries. Old cache versions are retained; there is no automatic cache
pruning.

Requests use one retry layer: SDK retries are disabled. Connection failures,
temporary rate limits, and server errors have bounded retries. `Retry-After` is
honored when it fits the retry budget. Invalid requests are not blindly retried;
authentication/access/quota errors stop the batch. Other file-specific failures
allow the next file to run unless `--stop-on-error` is supplied.

`--attempts` means total attempts including the first. `--read-timeout` bounds an
individual network read. `--request-deadline` is checked between received blocks,
so a blocked read can extend past it until the read timeout. `--retry-budget`
prevents starting retries after the budget; it is not a hard wall-clock kill for
an in-progress request.

Input/output collisions are checked before synthesis. OS-backed locks prevent
simultaneous processes from writing the same job/output. Temporary files and
atomic replacement protect previous finished recordings. All new outputs are
validated before publication, but publication of a final MP3 plus multiple
chapter files is **not** a single filesystem transaction.

## Outputs

Without `--output-dir`, outputs are written next to each source:

```text
notes_a_review_FINAL.mp3
notes_a_review_audio_v3/
    owner.json
    current_plan.json
    completed.json
    progress.json
    last_run.json
    run.log
    cache/
        <request-hash>.wav
        <request-hash>.json
        <request-hash>.split.json       # only after a length rejection
    plans/
        <plan-hash>/
            prepared.txt
            changes.diff
            plan.json
```

The complete batch report is stored in `.tts_runs/` under `--output-dir`, or the
current directory when no output directory was supplied. Reports include failed
and not-run files as well as partial progress. No credentials are intentionally
written to logs. Prepared text and reports may contain private course material;
keep the output directory private when appropriate.

A file with a matching fingerprint and validated final output is skipped without
making a speech request. A corrupt/missing cache chunk is regenerated when it is
needed; it is never accepted simply because a nonempty file exists. A valid
completed output can still be skipped even if its intermediate cache was deleted.

WAV caches are larger than MP3 caches. Keep them for low-cost retries and local
reassembly. Deleting them removes the ability to rebuild or revise the recording
without further speech API calls.

## Optional chapter exports

```bash
python Audioconversion_generic_v3.py --chapters
```

This still creates one complete MP3 per input, and also creates:

```text
notes_a_review_FINAL_chapters/
    001_Introduction.mp3
    002_Summary.mp3
    003_SECTION_TITLE.mp3
    ...
    chapters.json
    playlist.m3u8
```

Each detected section is synthesized separately (or split further when too
large). **Chapter mode can make more API requests** and uses a different chunk
plan than normal mode; switching it on can require new synthesis. Sections are
not timestamps guessed from text: chapter positions are calculated from actual
PCM sample counts after synthesis. Chapter MP3s are encoded from the same
assembled/normalized master as the complete recording. Some players may report
small differences due to MP3 encoder padding.

Use the playlist for the current chapter order. During a chapter-mode rebuild,
obsolete chapter tracks are removed only when they were recorded as generated
by this job and their contents are still unchanged. Modified old chapter files
are preserved. Turning chapter mode off leaves any prior chapter folder alone.

## Optional pronunciation dictionary

The bundled `pronunciations.example.json` is an opt-in starting point for common
engineering initialisms. It is never loaded automatically:

```bash
python Audioconversion_generic_v3.py \
    --pronunciations pronunciations.example.json \
    --dry-run --export-plan --show-chunks
```

Remove `--dry-run` to generate after reviewing it.

Rules are case-sensitive, whole-token matches at alphanumeric boundaries,
longest-first, and applied in one pass. For example, a rule for `ML` does not alter
`HTML`; a rule for `FIR` does not automatically alter `FIRs`. The replacement
result is not passed through the dictionary again. A custom dictionary can still
change technical meaning, so review the saved prepared text/diff.

Do not automatically replace every uppercase letter; many are variables.
Prefer letter-by-letter initialisms to guessed expansions with multiple meanings.

## Settings and useful commands

Copy `tts_config.example.json` to a new settings file, edit it, and explicitly
select it:

```bash
python Audioconversion_generic_v3.py --config tts_config.json
```

Command-line flags override JSON settings. Relative pronunciation-file paths in
JSON are resolved relative to that JSON file. No configuration file is silently
loaded, and credential keys are rejected as unsupported configuration settings.

Inspect exact requests:

```bash
python Audioconversion_generic_v3.py --dry-run --export-plan --show-chunks
```

Rebuild from cached audio only, without a key or speech API calls:

```bash
python Audioconversion_generic_v3.py notes_a_review.txt --assemble-only --rebuild
```

Supply the same model/voice/text-related settings and output directory used for
the original run. Missing cached chunks cause an error rather than a paid call.

Change only local audio finishing while reusing chunks:

```bash
python Audioconversion_generic_v3.py notes_a_review.txt --assemble-only --no-normalize
```

Force new synthesis (paid):

```bash
python Audioconversion_generic_v3.py notes_a_review.txt --restart
```

`--restart` always forces regeneration for that invocation; it is not needed to
resume an ordinary interrupted run. After an interrupted forced restart, an older
valid completed output may still exist. Use `--rebuild` to assemble available
validated chunks, or `--restart` again to request another full regeneration.

After deliberately moving a complete project directory to a new computer/path:

```bash
python Audioconversion_generic_v3.py --adopt-moved-cache --assemble-only --rebuild
```

V3 normally rejects a cache owned by another source path. Adoption explicitly
changes that ownership. Moved final outputs must match their previously recorded
checksums. Recreate the Python virtual environment on the new machine; do not
transfer the old `.venv` as an installation method.

Full option reference:

```bash
python Audioconversion_generic_v3.py --help
```

## Troubleshooting

**Externally managed environment / missing `python` or `openai`:**

```bash
cd ~/tts
source .venv/bin/activate
python -m pip install --upgrade openai tiktoken
```

Or use `~/tts/.venv/bin/python` explicitly instead of activating the environment.

**Existing output is not owned by this V3 job:** use `--output-dir ./audio_v3`, or
explicitly choose `--overwrite` to replace an old V2 or other recording.

**Too many small chunks / byte-budget warning:** install tiktoken and rerun a dry
run. The tokenizer may need internet access on first use to download its data.

**Wrong or missing API key:** the setup checker only checks presence. Actual
credentials, account access, model availability, and quota are checked by the
service on a real generation request. Never share the key in error reports.

**No notes in the menu:** put `.txt`/`.md` files beside the script, provide paths
explicitly, or use `--input-dir`. Files are not discovered recursively by default.

**Unwanted section detection:** inspect `--dry-run --show-chunks`, or disable
heuristics with `--headings none`. Source wording is preserved either way.

**One course failed:** rerun the same command. Valid finished outputs and cached
chunks are reused. Check `run.log`, `last_run.json`, and the batch JSON report.

**Another process is using the job:** close or allow that process to finish.
Do not delete a live lock file. The operating system releases the lock when the
process exits, although the harmless lock file remains.

**Interrupted run:** press Ctrl+C once and allow cleanup. Ordinary reruns resume
validated chunks. An interrupted final encode does not replace a previous final
MP3 with a partial file.

**Model/voice mismatch:** Cedar is for the GPT-4o mini TTS family. The legacy
`tts-1`/`tts-1-hd` models require a compatible legacy voice and cannot use custom
narration instructions. The script validates those combinations locally.

## Tests and limitations

Run the included offline regression tests:

```bash
python -m unittest -v test_tts_v3.py
```

The tests use generated tones and simulated speech responses, never the paid
speech API. Optional sample tests discover `*REBUILT.txt` in `TTS_SAMPLE_DIR` or
the package's parent directory. They skip when those user-provided files are not
present; the package deliberately does not bundle private source notes.

See `TEST_RESULTS.md` for the actual test conditions and results. The real
OpenAI service and subjective voice/pronunciation quality were not tested here.
Audio decoding, duration checks, and checksums cannot prove that a model narrated
every word correctly. Listen to the beginning, technical expressions, and request
joins in the first real recording before doing a large weekly batch.

Very large WAV masters approaching RIFF's 4 GiB limit are outside this script's
intended lecture-note use. There is no concurrency, automatic semantic rewriting,
speech-recognition verification, or embedded audiobook chapter format.

## API and implementation references

Verified against the official documentation when this version was prepared:

- Speech API: `https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create`
- TTS guide: `https://developers.openai.com/api/docs/guides/text-to-speech`
- Model limits/snapshots: `https://developers.openai.com/api/docs/models/gpt-4o-mini-tts`
- SDK streaming/retries: `https://github.com/openai/openai-python`
- Tokenizer mapping: `https://github.com/openai/tiktoken/blob/main/tiktoken/model.py`
- Loudness normalization: `https://ffmpeg.org/ffmpeg-filters.html#loudnorm`
- WAV format handling: `https://ffmpeg.org/ffmpeg-formats.html#wav`

Generated audio is AI-generated narration. Keep that clear when sharing it.
