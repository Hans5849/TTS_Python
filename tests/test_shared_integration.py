from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from speech_common.coordination import FileLock
from tts_python.config import load_config, ConfigError, default_config_path
from tts_python.processor import Processor
from tts_python.cli import main
from tts_python.status import service_state
from tts_python.tts.cloud import OpenAIEngine


def config(tmp_path):
    values = {'paths': {key: str(tmp_path/key) for key in ('inbox','outbox','processed','archive','failed','state','logs')},
              'processing': {'mode':'local'}, 'tts': {'preferred':'local', 'fallback':'none'}}
    path = tmp_path/'config.yaml'; path.write_text(yaml.safe_dump(values)); return path


class Local:
    is_cloud = False
    def synthesize(self, text, output): output.write_bytes(text.encode())


def test_yaml_and_new_environment_override(tmp_path, monkeypatch):
    path=config(tmp_path); monkeypatch.setenv('TTS_CONFIG',str(path))
    assert default_config_path() == path
    assert load_config(path).paths.inbox == tmp_path/'inbox'


def test_nested_yaml_typo_rejected(tmp_path):
    path=config(tmp_path); values=yaml.safe_load(path.read_text()); values['tts']['local']={'voic':'wrong'}
    path.write_text(yaml.safe_dump(values))
    with pytest.raises(ConfigError,match='unknown provider'): load_config(path)


def test_equal_basenames_do_not_overwrite_outputs(tmp_path):
    cfg=load_config(config(tmp_path)); engine=Processor(cfg,{}, {'local':Local()})
    a=tmp_path/'a/notes.txt'; b=tmp_path/'b/notes.txt'
    for p in (a,b): p.parent.mkdir(); p.write_text('same speech')
    first,second=engine.convert(a),engine.convert(b)
    assert first.audio != second.audio
    assert first.audio.is_file() and second.audio.is_file()


def test_manual_conversion_respects_worker_instance_lock(tmp_path,capsys):
    path=config(tmp_path); cfg=load_config(path); source=tmp_path/'test.txt'; source.write_text('hello')
    with FileLock(cfg.paths.state/'worker.lock'):
        assert main(['--config',str(path),'convert',str(source),'--private']) == 2
    assert 'busy' in capsys.readouterr().err


def test_openai_client_is_lazy_and_reused(tmp_path,monkeypatch):
    clients=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def stream_to_file(self,path): path.write_bytes(b'audio')
    def client(app):
        assert app=='tts'; clients.append(app)
        return SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(with_streaming_response=SimpleNamespace(create=lambda **_:Response()))))
    monkeypatch.setattr('tts_python.tts.cloud.openai_client',client)
    engine=OpenAIEngine('model','voice'); assert clients==[]
    engine.synthesize('one',tmp_path/'one.mp3'); engine.synthesize('two',tmp_path/'two.mp3')
    assert clients==['tts']


def test_service_command_uses_canonical_name(monkeypatch):
    calls=[]
    monkeypatch.setattr('tts_python.status.shutil.which',lambda _: 'systemctl')
    monkeypatch.setattr('tts_python.status.Path.is_dir',lambda _:True)
    monkeypatch.setattr('tts_python.status.subprocess.run',lambda args,**_: (calls.append(args) or SimpleNamespace(stdout='active')))
    assert service_state()=='ACTIVE'
    assert calls[0][-1]=='tts-python.service'
