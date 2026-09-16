# Deployment acceptance checklist

- Merge/install Speech_Common first. Install only this app, then install its sibling; both services remain independent.
- Record inbox/outbox hashes before upgrade/fresh/remove and compare afterward, including hidden databases, archived originals, failed inputs, and processed text.
- Test non-default directories, a stopped service upgrade, broken app venv, failed dependency build, invalid YAML, and failed activation rollback.
- Verify aliases start one worker only. A second foreground worker must fail without recovering the live worker's jobs.
- Exercise shared credential rotation and app override; private TTS must not make cloud requests. STT needs no key.
- On the target GPU host, verify physical UUID versus CUDA ordinal, actual memory release, waiter yield, and independent-GPU concurrent work.
- External Ollama/Plex/Folding processes do not participate in advisory locks. Configure their GPU placement/residency explicitly.
- Review removal dry-run. Uninstall only one application and run the sibling again. Shared purge must refuse a remaining consumer.

The test suite validates fake-engine/control-flow behavior and temporary-host installation transactions. It is not evidence of target-server CUDA/systemd/WSL validation.
