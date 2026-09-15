from __future__ import annotations
from .config import AppConfig
from .database import JobStore


def dashboard(config: AppConfig, store: JobStore) -> str:
    counts = store.counts()
    return f"""Audioconversion Server

Directories
  Inbox:           {config.paths.inbox}
  Outbox:          {config.paths.outbox}
  Processed:       {config.paths.processed}

Queue
  Waiting:         {counts.get('waiting', 0)}
  Processing:      {counts.get('processing', 0)}
  Completed:       {counts.get('completed', 0)}
  Failed:          {counts.get('failed', 0)}

Processing
  Mode:            {config.processing_mode.upper()}
  Local rules:     ENABLED

LLM
  Preferred:       {config.llm.preferred.upper()}
  Local provider:  {config.llm.local.provider or 'none'}
  Local model:     {config.llm.local.model or 'not configured'}

TTS
  Preferred:       {config.tts.preferred.upper()}
  Local provider:  {config.tts.local.provider or 'none'}
  Cloud provider:  {config.tts.cloud.provider or 'none'}

Privacy
  Private inbox:   CLOUD DISABLED

Cache
  Enabled:         {'YES' if config.cache_enabled else 'NO'}"""
