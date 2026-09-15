from pathlib import Path

import pytest

from audioconversion.chunker import chunk_text
from audioconversion.config import load_config
from audioconversion.normalizer import normalize
from audioconversion.processor import Processor
from audioconversion.router import PrivacyError, candidates, execute_with_fallback


class Engine:
    def __init__(self, cloud=False, fail=False):
        self.is_cloud, self.fail = cloud, fail

    def synthesize(self, text, output):
        if self.fail:
            raise RuntimeError("unavailable")
        output.write_bytes(b"audio:" + text.encode())


class LLM:
    is_cloud = False

    def process(self, text):
        return "spoken " + text


def config_file(tmp_path: Path, *, mode="hybrid", format="mp3") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'''[paths]
inbox="{tmp_path}/inbox"
outbox="{tmp_path}/outbox"
processed="{tmp_path}/processed"
archive="{tmp_path}/archive"
failed="{tmp_path}/failed"
state="{tmp_path}/state"
logs="{tmp_path}/logs"
[processing]
mode="{mode}"
[llm]
preferred="local"
fallback="none"
[tts]
preferred="local"
fallback="cloud"
[output]
format="{format}"
''')
    return path


def test_normalization_is_conservative():
    assert normalize("# Topic\r\n\r\nx ≤ 2 and \\alpha") == "Topic\n\nx less than or equal to 2 and alpha"


def test_hybrid_classifier_routes_only_complex_content():
    assert not chunk_text("Normal prose.")[0].semantic_processing
    assert chunk_text("$$ x = \\frac{1}{2} $$")[0].semantic_processing


def test_private_route_filters_cloud_and_fails_closed():
    local, cloud = Engine(), Engine(cloud=True)
    assert candidates("cloud", "local", {"local": local, "cloud": cloud}, True) == [local]
    with pytest.raises(PrivacyError):
        candidates("cloud", "none", {"cloud": cloud}, True)


def test_fallback_is_explicitly_logged(caplog):
    good = Engine()
    assert execute_with_fallback([Engine(fail=True), good], lambda item: item.synthesize("x", Path("/dev/null")),
                                 __import__("logging").getLogger("test")) is None
    assert "Falling back" in caplog.text


def test_pipeline_preserves_speech_ready_and_audio(tmp_path):
    config = load_config(config_file(tmp_path))
    source = tmp_path / "lecture.txt"
    source.write_text("Ordinary prose.\n\n$$ x = \\frac{1}{2} $$")
    result = Processor(config, {"local": LLM()}, {"local": Engine()}).convert(source)
    assert result.speech_ready.read_text().startswith("Ordinary prose.\n\nspoken")
    assert result.audio.read_bytes().startswith(b"audio:")


def test_private_pipeline_never_calls_cloud(tmp_path):
    config = load_config(config_file(tmp_path, mode="local"))
    source = tmp_path / "private.txt"
    source.write_text("Sensitive content.")
    with pytest.raises(PrivacyError):
        Processor(config, {}, {"cloud": Engine(cloud=True)}).convert(source, private=True)
