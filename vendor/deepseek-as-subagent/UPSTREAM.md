# Vendored execution engine

Source: https://github.com/PsChina/deepseek-as-subagent

Commit: `7e54026392f85ccfc796f947faf9ffac3968a49e` (2026-10-10)

The dependency lock is copied unchanged. See LICENSE.
CodexRelay's adapter lives in engine/codexrelay and imports this engine.
Do not replace this snapshot without reviewing upstream changes and running integration tests.

## CodexRelay 0.1.1 patches

- `history_compaction.py`: compact old completed tool exchanges; retain original user/system/steering messages and recent complete exchanges.
- `agent_loop.py`: bounded history compaction, smaller reservations after history shrink, adaptive output reservation within the remaining cumulative budget, explicit local-budget metrics, native Windows command guidance.
- `config.py`: configurable `max_total_tokens` (default remains 1,000,000).
- `provider_child.py` / `provider_process.py`: distinguish explicit provider quota/billing failures from rate limits, without returning raw provider error text or retrying non-transient quota failures.

No path boundary, command isolation, mutation journal, acknowledgement or execution-lease checks were removed.

## CodexRelay 0.1.2 patches

Thinking-mode compaction preserves every original assistant message including full opaque `reasoning_content`, tool IDs and arguments. Only older tool outputs are shortened; no synthetic assistant archive is added in thinking mode. Provider errors referring to a missing reasoning-history field receive a safe diagnostic category without returning raw provider text.
