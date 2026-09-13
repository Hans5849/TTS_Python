"""Offline regression tests; simulated speech responses, never paid API calls.

Run: python -m unittest -v test_tts_v3.py
Sample-file tests run when the six optional *REBUILT.txt files are present in
TTS_SAMPLE_DIR (or the package's parent directory). Otherwise those tests skip.
"""
from contextlib import contextmanager, redirect_stdout
from dataclasses import asdict
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

import Audioconversion_generic_v3 as tts


def arguments(*extra):
    return tts.parse_args(["--token-counter", "bytes", "--no-normalize", "--rpm", "600", *extra])


def synthetic_wav(duration=1.2):
    output = io.BytesIO()
    with wave.open(output, "wb") as f:
        tts.configure_wave(f)
        # Alternating-amplitude sine wave: deterministic synthetic audio, not speech.
        values = []
        for i in range(int(duration * tts.SAMPLE_RATE)):
            amplitude = 5000 if (i // 6000) % 2 else 1800
            values.append(round(amplitude * math.sin(2 * math.pi * 440 * i / tts.SAMPLE_RATE)))
        f.writeframes(struct.pack("<" + "h" * len(values), *values))
    return output.getvalue()


class FakeError(Exception):
    def __init__(self, code, body="test error", headers=None):
        super().__init__(body)
        self.status_code = code
        self.body = body
        self.response = type("Response", (), {"headers": headers or {}})()


class Response:
    headers = {"x-request-id": "offline-test-request"}
    def __init__(self, audio, interrupt=False):
        self.audio = audio
        self.interrupt = interrupt
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def iter_bytes(self, chunk_size):
        yield self.audio[:100]
        if self.interrupt:
            raise KeyboardInterrupt()
        yield self.audio[100:]


class FakeClient:
    def __init__(self, failure=None, duration=1.2):
        self.calls = []
        self.failure = failure
        self.audio_bytes = synthetic_wav(duration)
        self.audio = type("Audio", (), {})()
        self.audio.speech = type("Speech", (), {})()
        self.audio.speech.with_streaming_response = self
    def create(self, **kwargs):
        assert "speed" not in kwargs
        self.calls.append(kwargs)
        if self.failure:
            problem = self.failure(kwargs, len(self.calls))
            if problem is not None:
                raise problem
        return Response(self.audio_bytes)
    def close(self):
        pass


class FakeProvider:
    def __init__(self, client):
        self.client = client
        self.gets = 0
    def get(self):
        self.gets += 1
        if self.client is None:
            raise AssertionError("No API client should have been requested")
        return self.client


class NoWait:
    def wait(self):
        pass


class TextTests(unittest.TestCase):
    def test_file_workers_validation(self):
        self.assertEqual(arguments().file_workers, 1)
        self.assertEqual(arguments("--file-workers", "3").file_workers, 3)
        for value in ("0", "33"):
            with self.assertRaisesRegex(tts.TTSError, "file-workers"):
                arguments("--file-workers", value)

    def test_file_workers_environment_and_cli_precedence(self):
        with patch.dict(os.environ, {"TTS_FILE_WORKERS": "4"}):
            self.assertEqual(arguments().file_workers, 4)
            self.assertEqual(arguments("--file-workers", "2").file_workers, 2)
        with patch.dict(os.environ, {"TTS_FILE_WORKERS": "many"}):
            with self.assertRaisesRegex(tts.TTSError, "TTS_FILE_WORKERS"):
                arguments()
            self.assertEqual(arguments("--file-workers", "2").file_workers, 2)

    def test_main_processes_separate_files_concurrently(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sources = []
            for index in range(3):
                source = root / f"notes_{index}.txt"
                source.write_text(f"Document {index}.")
                sources.append(str(source))
            active = 0
            peak = 0
            lock = threading.Lock()

            def fake_process(plan, paths, args, budget, provider, limiter, report):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.05)
                report.update({"status": "ok", "generated": 0, "cached": 0, "requests": 0})
                with lock:
                    active -= 1

            argv = [*sources, "--output-dir", str(root / "out"), "--token-counter", "bytes",
                    "--no-normalize", "--file-workers", "2"]
            with patch.object(tts, "dependency_check"), patch.object(
                    tts, "process_plan", side_effect=fake_process), redirect_stdout(io.StringIO()):
                result = tts.main(argv)
            self.assertEqual(result, 0)
            self.assertEqual(peak, 2)

    def test_tokenizer_path_with_local_test_double(self):
        class LocalEncoding:
            def encode(self, text, disallowed_special=()):
                return list(range(len(text.encode("utf-8"))))
        fake_module = type("Tokenizer", (), {"get_encoding": lambda name: LocalEncoding()})
        with patch.dict(sys.modules, {"tiktoken": fake_module}):
            a = arguments("--token-counter", "tiktoken")
            b = tts.TokenBudget(a)
            self.assertIsNotNone(b.encoding)
            self.assertEqual(b.count("abc"), 3)
            text = "Keep every complete word. " * 600
            chunks = tts.split_naturally(text, b)
            self.assertEqual(" ".join(chunks).split(), text.split())
            self.assertTrue(all(b.fits(c) for c in chunks))

    def test_heading_without_blank_line(self):
        text = "# First section\nText here.\n## Second section\nMore text."
        self.assertEqual([x.title for x in tts.detect_sections(text, "auto")], ["First section", "Second section"])

    def test_unknown_encoding_is_actionable(self):
        with self.assertRaises(tts.TTSError):
            arguments("--encoding", "not-a-real-encoding")

    def test_preserve_operators(self):
        text = "- x + 2\n\n> 0\n\n+ y = z\n\nx[n] and H(z) and 10^-3\n"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notes.txt"
            p.write_text(text)
            args = arguments()
            plan = tts.prepare_plan(p, args, tts.TokenBudget(args), {})
            self.assertEqual(plan.prepared, text.strip())
            self.assertEqual(tts.content_key(text), tts.content_key("".join(c.text for c in plan.chunks)))

    def test_pronunciation_boundaries(self):
        result, counts = tts.apply_pronunciations("HTML and ML; DSP, SDSP and FIRs FIR", {
            "ML": "machine learning", "DSP": "D S P", "FIR": "F I R"})
        self.assertEqual(result, "HTML and machine learning; D S P, SDSP and FIRs F I R")
        self.assertEqual(counts, {"ML": 1, "DSP": 1, "FIR": 1})

    def test_pronunciations_non_cascading(self):
        result, _ = tts.apply_pronunciations("A B C", {"A": "B", "B": "C"})
        self.assertEqual(result, "B C C")

    def test_case_sensitive_units(self):
        result, _ = tts.apply_pronunciations("MHz mhz GHz gHz", {"MHz": "megahertz", "GHz": "gigahertz"})
        self.assertEqual(result, "megahertz mhz gigahertz gHz")

    def test_markdown_cleanup_only_explicit_headings(self):
        text = "# Heading\n\n- x + 2\n> 0\n+ y\n\n```python\n# comment\nx = 2\n```\n\n$$\n# math\n$$"
        result = tts.strip_heading_markers(text)
        self.assertTrue(result.startswith("Heading"))
        self.assertIn("# comment", result)
        self.assertIn("# math", result)
        self.assertIn("- x + 2\n> 0\n+ y", result)

    def test_oversized_word_rejected_not_cut(self):
        a = arguments("--max-chars", "200")
        with self.assertRaises(tts.TTSError):
            tts.split_naturally("word" * 500, tts.TokenBudget(a))

    def test_near_capacity_short_words_remain_whole(self):
        a = arguments("--max-chars", "3800")
        text = ("abcdefghij " * 691)[:7597]
        result = tts.split_naturally(text, tts.TokenBudget(a))
        self.assertEqual(" ".join(result).split(), text.split())
        self.assertTrue(all(tts.TokenBudget(a).fits(x) for x in result))

    def test_7597_chars_allow_extra_request_instead_of_cut_word(self):
        a = arguments("--max-chars", "3800")
        b = tts.TokenBudget(a)
        b.max_tokens = 100000  # Isolate the exact character-boundary regression.
        text = ("abcdefghij " * 691)[:7597]
        chunks = tts.split_naturally(text, b)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(" ".join(chunks).split(), text.split())
        self.assertTrue(all(len(c) <= 3800 for c in chunks))

    def test_multibyte_token_budget(self):
        a = arguments()
        text = ("\u03b8 \u03c9 \u03bb \u2211 mathematical quantities. " * 300).strip()
        b = tts.TokenBudget(a)
        chunks = tts.split_naturally(text, b)
        self.assertTrue(all(b.total(c) <= b.max_tokens for c in chunks))
        self.assertEqual(text.split(), " ".join(chunks).split())

    def test_headings_and_source_notes(self):
        text = "Title\nSubtitle\n\nSummary\n\nIntroduction text.\n\nHEAPS\n\nThe tree.\n\nSource note:\n\nUncertain coverage."
        sections = tts.detect_sections(text, "auto")
        self.assertEqual([s.title for s in sections], ["Introduction", "Summary", "HEAPS", "Source note:"])
        self.assertEqual(tts.content_key(text), tts.content_key("".join(s.text for s in sections)))

    def test_fenced_code_not_headings(self):
        text = "# Heading\n\nText.\n\n```\n\nCODE TEXT\n\n```\n\nMORE MATERIAL\n\nBody."
        sections = tts.detect_sections(text, "auto")
        self.assertEqual([s.title for s in sections], ["Heading", "MORE MATERIAL"])

    def test_config_hash_changes_with_settings(self):
        a = arguments()
        x = tts.digest_json(tts.request_spec("Hello", a))
        a.voice = "marin"
        self.assertNotEqual(x, tts.digest_json(tts.request_spec("Hello", a)))
        a.voice = "cedar"
        a.instructions += " Please."
        self.assertNotEqual(x, tts.digest_json(tts.request_spec("Hello", a)))

    def test_config_wrong_types_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            for value in ({"normalize": "false"}, {"api_key": "secret"}, {"max_chars": -1}, {"rpm": float("inf")}):
                p.write_text(json.dumps(value))
                with self.assertRaises(tts.TTSError):
                    tts.parse_args(["--config", str(p)])

    def test_config_cli_precedence(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_text(json.dumps({"voice": "marin", "normalize": False}))
            a = tts.parse_args(["--config", str(p), "--voice", "cedar"])
            self.assertEqual(a.voice, "cedar")
            self.assertFalse(a.normalize)

    def test_model_compatibility(self):
        with self.assertRaises(tts.TTSError):
            tts.parse_args(["--model", "tts-1"])
        a = tts.parse_args(["--model", "tts-1", "--voice", "alloy"])
        self.assertEqual(tts.request_spec("hi", a)["instructions"], "")

    def test_json_array_is_not_a_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "manifest.json"
            p.write_text("[]")
            self.assertIsNone(tts.read_object(p))
            p.write_text("{bad")
            self.assertIsNone(tts.read_object(p))

    def test_selection(self):
        self.assertEqual(tts.parse_selection("3,1-2,2", 3), [2, 0, 1])
        self.assertEqual(tts.parse_selection("all", 6), list(range(6)))
        self.assertEqual(tts.parse_selection("3-1", 3), [2, 1, 0])
        with self.assertRaises(tts.TTSError):
            tts.parse_selection("4", 3)

    def test_collision_detection(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sources = [root / "course-a" / "week.txt", root / "course-b" / "week.txt"]
            for p in sources:
                p.parent.mkdir()
                p.write_text("Hello")
            a = arguments("--output-dir", str(root / "out"))
            with self.assertRaises(tts.TTSError):
                tts.collision_check(sources, [tts.paths_for(p, a) for p in sources], a)

    def test_discovery_excludes_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for path in ("notes.txt", "README.md", "requirements-tts.txt", "old_audio_v3/prepared.txt", ".venv/hello.txt"):
                p = root / path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("test")
            a = arguments("--input-dir", str(root), "--recursive")
            self.assertEqual([p.name for p in tts.discover_files(root, a)], ["notes.txt"])

    def test_retry_classification(self):
        self.assertEqual(tts.error_kind(FakeError(401)), "fatal")
        self.assertEqual(tts.error_kind(FakeError(429, "insufficient_quota")), "fatal")
        self.assertEqual(tts.error_kind(FakeError(429)), "retry")
        self.assertEqual(tts.error_kind(FakeError(500)), "retry")
        self.assertEqual(tts.error_kind(FakeError(400, "context_length_exceeded")), "length")
        self.assertEqual(tts.error_kind(FakeError(400, "bad voice")), "fail")
        self.assertEqual(tts.error_kind(OSError("disk full")), "fail")
        self.assertEqual(tts.retry_after(FakeError(429, headers={"retry-after": "2"})), 2)

    def test_secrets_redacted(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "super-secret-key"}):
            self.assertNotIn("super-secret-key", tts.safe_error(Exception("Oops super-secret-key")))
        self.assertNotIn("sk-abcdef123", tts.safe_error(Exception("Oops sk-abcdef123")))

    def test_dotenv_loads_supported_values_without_overwriting_environment(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".env").write_text(
                "# credentials\nexport OPENAI_API_KEY='file-key'\n"
                'OPENAI_BASE_URL="https://example.com/v1"\nTTS_FILE_WORKERS=3\nIGNORED=value\n'
            )
            with patch.object(Path, "cwd", return_value=root), patch.dict(
                    os.environ, {"OPENAI_API_KEY": "shell-key"}, clear=True):
                tts.load_dotenv(root)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "shell-key")
                self.assertEqual(os.environ["OPENAI_BASE_URL"], "https://example.com/v1")
                self.assertEqual(os.environ["TTS_FILE_WORKERS"], "3")
                self.assertNotIn("IGNORED", os.environ)

    def test_dotenv_rejects_empty_supported_value(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".env").write_text("OPENAI_API_KEY=\n")
            with patch.object(Path, "cwd", return_value=root), patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(tts.TTSError, "OPENAI_API_KEY is empty"):
                    tts.load_dotenv(root)

    def test_main_defaults_outputs_to_audio_beside_script(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            script_dir = root / "tts"
            source_dir = root / "sources"
            script_dir.mkdir()
            source_dir.mkdir()
            source = source_dir / "notes.txt"
            source.write_text("A short test document.")
            fake_script = script_dir / "Audioconversion_generic_v3.py"
            with patch.object(tts, "__file__", str(fake_script)), redirect_stdout(io.StringIO()):
                result = tts.main([str(source), "--dry-run", "--export-plan", "--token-counter", "bytes"])
            self.assertEqual(result, 0)
            self.assertTrue(list((script_dir / "audio" / "notes_audio_v3" / "plans").iterdir()))
            self.assertFalse((source_dir / "audio").exists())

    def test_process_timeout(self):
        with self.assertRaises(tts.AudioError):
            tts.run_process([sys.executable, "-c", "import time; time.sleep(1)"], 0.01)

    def test_lock_excludes_second_process(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".lock"
            code = "from pathlib import Path; import Audioconversion_generic_v3 as t;\nwith t.file_lock(Path(__import__('sys').argv[1])): print('bad')"
            with tts.file_lock(path):
                p = subprocess.run([sys.executable, "-c", code, str(path)],
                                   cwd=Path(tts.__file__).parent, capture_output=True, text=True)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("Another process", p.stderr)
            with tts.file_lock(path):
                pass

    def test_user_samples_content_and_sections(self):
        directory = Path(os.getenv("TTS_SAMPLE_DIR", str(Path(tts.__file__).parent.parent)))
        samples = sorted(directory.glob("*REBUILT.txt"))
        if not samples:
            self.skipTest("Optional user-sample files not present")
        self.assertEqual(len(samples), 6)
        for chapter_mode in (False, True):
            a = arguments(*(["--chapters"] if chapter_mode else []))
            b = tts.TokenBudget(a)
            for source in samples:
                with self.subTest(file=source.name, chapters=chapter_mode):
                    plan = tts.prepare_plan(source, a, b, {})
                    self.assertEqual(plan.prepared, tts.normalize_source(source.read_text()))
                    self.assertEqual(tts.content_key(plan.prepared),
                                     tts.content_key("".join(c.text for c in plan.chunks)))
                    self.assertTrue(all(b.fits(c.text) for c in plan.chunks))
                    self.assertGreater(len(plan.sections), 5)
                    if "Preview" in source.name:
                        self.assertTrue(any(s.title.startswith("Epilogue:") for s in plan.sections))
                    if source.name.startswith("ENEE641_Week2"):
                        self.assertTrue(any(s.title == "Source note:" for s in plan.sections))
                    if chapter_mode:
                        self.assertTrue(all(len(c.sections) == 1 for c in plan.chunks))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "audio").mkdir()
        self.args = arguments()
        self.args.output_dir = str(self.root / "audio")
        self.budget = tts.TokenBudget(self.args)
        self.cache = self.root / "cache"
        self.reporter = tts.Reporter()
    def tearDown(self):
        self.temp.cleanup()
    def obtain(self, text, fake=None, args=None, stats=None, seen=None):
        return tts.obtain_audio(text, self.cache, args or self.args,
                                tts.TokenBudget(args or self.args),
                                FakeProvider(fake), NoWait(), stats or tts.Stats(),
                                self.reporter, {} if seen is None else seen)
    def plan(self, text="TITLE\n\nFirst paragraph.\n\nANOTHER SECTION\n\nSecond paragraph.", args=None):
        p = self.root / "notes.txt"
        p.write_text(text)
        a = args or self.args
        return tts.prepare_plan(p, a, tts.TokenBudget(a), {})

    def test_streaming_wav_unknown_lengths(self):
        fake = FakeClient()
        original = fake.audio_bytes
        data = bytearray(original)
        struct.pack_into("<I", data, 4, 0xffffffff)
        struct.pack_into("<I", data, 40, 0xffffffff)
        fake.audio_bytes = bytes(data)
        parts = self.obtain("Stream header regression.", fake)
        self.assertEqual(parts[0].frames, round(1.2 * tts.SAMPLE_RATE))

    def test_finite_truncated_wav_rejected(self):
        path = self.root / "truncated.wav"
        path.write_bytes(synthetic_wav()[:-100])
        with self.assertRaises(tts.AudioError):
            tts.repair_streaming_wav(path)

    def test_normal_wav_header_is_unchanged(self):
        path = self.root / "ordinary.wav"
        data = synthetic_wav()
        path.write_bytes(data)
        self.assertFalse(tts.repair_streaming_wav(path))
        self.assertEqual(data, path.read_bytes())

    def test_moved_project_can_be_adopted(self):
        plan = self.plan()
        paths = tts.paths_for(plan.source, self.args)
        with redirect_stdout(io.StringIO()):
            tts.process_plan(plan, paths, self.args, self.budget, FakeProvider(FakeClient()), NoWait(), {})
        moved = self.root / "moved"
        moved.mkdir()
        shutil.move(str(plan.source), moved)
        shutil.move(str(self.root / "audio"), moved)
        source = moved / "notes.txt"
        a = arguments("--adopt-moved-cache", "--assemble-only",
                      "--output-dir", str(moved / "audio"))
        new_plan = tts.prepare_plan(source, a, tts.TokenBudget(a), {})
        new_paths = tts.paths_for(source, a)
        tts.collision_check([source], [new_paths], a)
        tts.ensure_output_allowed(new_paths, a, new_plan)
        report = {}
        with redirect_stdout(io.StringIO()):
            tts.process_plan(new_plan, new_paths, a, tts.TokenBudget(a), FakeProvider(None), NoWait(), report)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["requests"], 0)

    def test_obsolete_chapters_removed_only_when_unchanged(self):
        a = arguments("--chapters")
        plan = self.plan(args=a)
        paths = tts.paths_for(plan.source, a)
        with redirect_stdout(io.StringIO()):
            tts.process_plan(plan, paths, a, tts.TokenBudget(a), FakeProvider(FakeClient()), NoWait(), {})
        old = paths.chapters / "002_ANOTHER_SECTION.mp3"
        self.assertTrue(old.exists())
        plan2 = self.plan(text="TITLE\n\nFirst paragraph.\n\nDIFFERENT SECTION\n\nSecond paragraph.", args=a)
        with redirect_stdout(io.StringIO()):
            tts.process_plan(plan2, paths, a, tts.TokenBudget(a), FakeProvider(FakeClient()), NoWait(), {})
        self.assertFalse(old.exists())
        self.assertTrue((paths.chapters / "002_DIFFERENT_SECTION.mp3").exists())

    def test_fatal_error_stops_remaining_batch(self):
        inputs = []
        for i in range(3):
            p = self.root / f"fatal_{i}.txt"
            p.write_text(f"Document {i}.")
            inputs.append(str(p))
        fake = FakeClient(lambda kw, n: FakeError(401, "bad key"))
        argv = [*inputs, "--output-dir", str(self.root / "out"), "--token-counter", "bytes", "--no-normalize"]
        with patch.object(tts.LazyClient, "get", lambda self: fake), patch.object(tts.RateLimiter, "wait", lambda self: None), redirect_stdout(io.StringIO()):
            result = tts.main(argv)
        self.assertEqual(result, 1)
        self.assertEqual(len(fake.calls), 1)
        report = json.loads(next((self.root / "out" / ".tts_runs").glob("*.json")).read_text())
        self.assertEqual([r["status"] for r in report["files"]], ["failed", "not-run", "not-run"])

    def test_generation_and_reuse(self):
        fake = FakeClient()
        stats = tts.Stats()
        original = self.obtain("A sample sentence.", fake, stats=stats)
        self.assertEqual(stats.generated, 1)
        self.assertEqual(len(fake.calls), 1)
        reused = self.obtain("A sample sentence.")
        self.assertEqual(original, reused)
        self.assertEqual(tts.probe_audio(original[0].path, 30)["codec"], "pcm_s16le")

    def test_corrupt_cache_not_reused(self):
        original = self.obtain("A sample sentence.", FakeClient())
        original[0].path.write_bytes(b"invalid")
        a = arguments("--assemble-only")
        with self.assertRaises(tts.TTSError):
            self.obtain("A sample sentence.", args=a)
        repaired = self.obtain("A sample sentence.", FakeClient())
        self.assertGreater(repaired[0].frames, 0)

    def test_later_cache_survives_earlier_failure(self):
        self.obtain("Third later part.", FakeClient())
        later = list(self.cache.glob("*.json"))
        a = arguments("--attempts", "1")
        with self.assertRaises(FakeError):
            self.obtain("Second failing part.", FakeClient(lambda kw, n: FakeError(500)), args=a)
        self.assertTrue(all(p.exists() for p in later))
        self.obtain("Third later part.")  # No client allowed; must use cached audio.

    def test_retry_count_no_nesting(self):
        fake = FakeClient(lambda kw, n: FakeError(500) if n < 3 else None)
        stats = tts.Stats()
        with patch.object(tts.time, "sleep", lambda n: None):
            self.obtain("Retry me.", fake, stats=stats)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(stats.requests, 3)

    def test_bad_key_not_retried(self):
        fake = FakeClient(lambda kw, n: FakeError(401))
        with self.assertRaises(tts.FatalAPIError):
            self.obtain("Bad key.", fake)
        self.assertEqual(len(fake.calls), 1)

    def test_invalid_request_not_retried(self):
        fake = FakeClient(lambda kw, n: FakeError(400, "invalid voice"))
        with self.assertRaises(FakeError):
            self.obtain("Bad request.", fake)
        self.assertEqual(len(fake.calls), 1)

    def test_adaptive_split_and_offline_resume(self):
        text = "First paragraph with words.\n\nSecond paragraph with words."
        fake = FakeClient(lambda kw, n: FakeError(400, "input too long") if n == 1 else None)
        parts = self.obtain(text, fake)
        self.assertGreater(len(parts), 1)
        self.assertTrue(list(self.cache.glob("*.split.json")))
        offline = self.obtain(text, args=arguments("--assemble-only"))
        self.assertEqual(parts, offline)

    def test_atomic_download_on_interrupt(self):
        text = "Existing recording."
        parts = self.obtain(text, FakeClient())
        checksum = tts.sha_file(parts[0].path)
        fake = FakeClient()
        fake.create = lambda **kw: Response(fake.audio_bytes, interrupt=True)
        # with_streaming_response references fake; dynamically replaced create used.
        with self.assertRaises(KeyboardInterrupt):
            self.obtain(text, fake, args=arguments("--restart"))
        self.assertEqual(tts.sha_file(parts[0].path), checksum)
        self.assertEqual(list(self.cache.glob(".download-*")), [])

    def test_unmanaged_final_requires_overwrite(self):
        plan = self.plan()
        paths = tts.paths_for(plan.source, self.args)
        paths.final.parent.mkdir(parents=True, exist_ok=True)
        paths.final.write_bytes(b"old final")
        with self.assertRaises(tts.TTSError):
            tts.ensure_output_allowed(paths, self.args)
        self.args.overwrite = True
        tts.ensure_output_allowed(paths, self.args)

    def test_final_assembly_failure_preserves_previous(self):
        plan = self.plan()
        paths = tts.paths_for(plan.source, self.args)
        paths.final.parent.mkdir(parents=True, exist_ok=True)
        paths.final.write_bytes(b"previous good output")
        old = paths.final.read_bytes()
        parts = [self.obtain(c.text, FakeClient()) for c in plan.chunks]
        with patch.object(tts, "encode_mp3", side_effect=tts.AudioError("simulated encode failure")):
            with self.assertRaises(tts.AudioError):
                tts.finalize(plan, paths, parts, self.args, self.reporter, "fingerprint")
        self.assertEqual(paths.final.read_bytes(), old)
        self.assertFalse((paths.work / "completed.json").exists())

    def test_chapters_and_normalization(self):
        a = arguments("--chapters", "--normalize", "--section-pause", "0.2")
        plan = self.plan(args=a)
        paths = tts.paths_for(plan.source, a)
        parts = [self.obtain(c.text, FakeClient(duration=4.0), args=a) for c in plan.chunks]
        record = tts.finalize(plan, paths, parts, a, self.reporter, "test-fingerprint")
        self.assertEqual(len(record["chapters"]), len(plan.sections))
        self.assertTrue(record["normalization"]["applied"])
        self.assertTrue(tts.completed_valid(record, "test-fingerprint", a))
        self.assertTrue((paths.chapters / "playlist.m3u8").exists())
        self.assertTrue(all(p.exists() for p in [Path(x["path"]) for x in record["outputs"]]))
        self.assertTrue(all(c["end_frame"] > c["start_frame"] for c in record["chapters"]))

    def test_complete_output_skips_client(self):
        plan = self.plan()
        paths = tts.paths_for(plan.source, self.args)
        provider = FakeProvider(FakeClient())
        with redirect_stdout(io.StringIO()):
            first = {}
            tts.process_plan(plan, paths, self.args, self.budget, provider, NoWait(), first)
            second = {}
            tts.process_plan(plan, paths, self.args, self.budget, FakeProvider(None), NoWait(), second)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["requests"], 0)

    def test_rebuild_does_not_need_api_key_or_sdk(self):
        plan = self.plan()
        paths = tts.paths_for(plan.source, self.args)
        with redirect_stdout(io.StringIO()):
            tts.process_plan(plan, paths, self.args, self.budget, FakeProvider(FakeClient()), NoWait(), {})
            a = arguments("--assemble-only", "--rebuild")
            report = {}
            tts.process_plan(plan, paths, a, tts.TokenBudget(a), FakeProvider(None), NoWait(), report)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["generated"], 0)
        self.assertGreater(report["cached"], 0)

    def test_malformed_completed_record_regenerates_no_crash(self):
        a = arguments("--overwrite", "--output-dir", str(self.root / "audio"))
        plan = self.plan(args=a)
        paths = tts.paths_for(plan.source, a)
        paths.work.mkdir()
        (paths.work / "completed.json").write_text("[]")
        with redirect_stdout(io.StringIO()):
            result = {}
            tts.process_plan(plan, paths, a, tts.TokenBudget(a), FakeProvider(FakeClient()), NoWait(), result)
        self.assertEqual(result["status"], "ok")

    def test_actual_sample_batch_pipeline_and_skip(self):
        directory = Path(os.getenv("TTS_SAMPLE_DIR", str(Path(tts.__file__).parent.parent)))
        samples = sorted(directory.glob("*REBUILT.txt"))
        if not samples:
            self.skipTest("Optional user-sample files not present")
        out = self.root / "sample-output"
        argv = [*[str(p) for p in samples], "--output-dir", str(out), "--token-counter", "bytes", "--no-normalize"]
        fake = FakeClient()
        with patch.object(tts.LazyClient, "get", lambda self: fake), patch.object(tts.RateLimiter, "wait", lambda self: None), redirect_stdout(io.StringIO()):
            result = tts.main(argv)
        self.assertEqual(result, 0)
        self.assertEqual(len(list(out.glob("*_FINAL.mp3"))), 6)
        with patch.object(tts.LazyClient, "get", side_effect=AssertionError("A completed sample requested an API client")), redirect_stdout(io.StringIO()):
            result = tts.main(argv)
        self.assertEqual(result, 0)
        for path in out.glob("*_audio_v3/last_run.json"):
            data = json.loads(path.read_text())
            self.assertEqual(data["status"], "skipped")
            self.assertEqual(data["requests"], 0)

    def test_six_file_batch_continues_after_one_failure(self):
        inputs = []
        for i in range(6):
            p = self.root / f"notes_{i}.txt"
            p.write_text(f"Batch test document {i}. Content stays separate.")
            inputs.append(str(p))
        fake = FakeClient(lambda kw, n: FakeError(400, "file-specific error") if "document 2" in kw["input"] else None)
        argv = [*inputs, "--output-dir", str(self.root / "out"), "--token-counter", "bytes", "--no-normalize"]
        with patch.object(tts.LazyClient, "get", lambda self: fake), patch.object(tts.RateLimiter, "wait", lambda self: None), redirect_stdout(io.StringIO()):
            code = tts.main(argv)
        self.assertEqual(code, 1)
        self.assertEqual(len(list((self.root / "out").glob("*_FINAL.mp3"))), 5)
        report = json.loads(next((self.root / "out" / ".tts_runs").glob("*.json")).read_text())
        self.assertEqual([r["status"] for r in report["files"]], ["ok", "ok", "failed", "ok", "ok", "ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
