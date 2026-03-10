# Concurrency Engine

## Overview

The Concurrency Engine provides **parallel model processing** with intelligent retry logic, multi-target broadcasting, resource management, and live progress tracking. It is designed to efficiently process large batches of semantic models across multiple target platforms simultaneously.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Concurrency Engine                       │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Worker Pool  │  │ Retry Logic  │  │ Progress Dashboard│   │
│  │ Management   │  │              │  │                   │   │
│  ├──────────────┤  ├──────────────┤  ├──────────────────┤   │
│  │ Auto-detect  │  │ Exponential  │  │ Rich live display│   │
│  │ CPU cores    │  │ backoff      │  │                   │   │
│  │ Adaptive     │  │ Max retries  │  │ Per-model status │   │
│  │ throttling   │  │ Jitter       │  │ ETA tracking     │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              Multi-Target Broadcasting                │    │
│  │                                                       │    │
│  │  Source ──▶ Model₁ ──▶ Target₁ (Snowflake)           │    │
│  │                   ──▶ Target₂ (Fabric)               │    │
│  │            Model₂ ──▶ Target₁ (Snowflake)           │    │
│  │                   ──▶ Target₂ (Fabric)               │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

## Features

### Parallel Model Processing

Process multiple semantic models concurrently using a `ThreadPoolExecutor`:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

class ConcurrencyEngine:
    def __init__(self, max_workers: int | None = None):
        self._max_workers = max_workers or self._auto_detect_workers()

    def _auto_detect_workers(self) -> int:
        """Auto-detect optimal worker count based on CPU cores."""
        import os
        cores = os.cpu_count() or 1
        return min(cores, 4)  # Cap at 4 to avoid resource saturation

    def process_models(self, models: list, pipeline_fn, **kwargs):
        """Process multiple models in parallel."""
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(pipeline_fn, model, **kwargs): model
                for model in models
            }
            results = {}
            for future in as_completed(futures):
                model = futures[future]
                try:
                    results[model] = future.result()
                except Exception as e:
                    results[model] = e
            return results
```

### Multi-Target Broadcasting

Deploy a single model to multiple target platforms:

```python
# One Source → Many Targets
targets = ["snowflake", "fabric", "databricks"]
for model in models:
    # Models deploy SEQUENTIALLY per target to avoid DDL race conditions
    for target in targets:
        deploy(model, target)
```

**Important:** Models are deployed **sequentially** per target to avoid DDL race conditions (e.g., CREATE OR REPLACE conflicts). Targets themselves can be processed in parallel.

### Retry Logic

Automatic retry with exponential backoff for transient failures:

```python
class RetryConfig:
    max_retries: int = 3
    base_delay: float = 1.0      # seconds
    max_delay: float = 60.0      # seconds
    backoff_factor: float = 2.0
    jitter: bool = True
    retryable_errors: tuple = (
        ConnectionError,
        TimeoutError,
        RateLimitError,
    )
```

### Resource Management

- **Adaptive worker pool**: Adjusts based on available CPU cores
- **Memory monitoring**: Warns when memory usage exceeds thresholds
- **Connection pooling**: Reuses database connections across workers
- **Graceful shutdown**: Completes in-progress work on interruption

### Progress Dashboard

Live progress display using Rich library:

```
┌─────────────────────────────────────────────────┐
│ SemaBridge - Processing 12 models               │
│                                                 │
│ ████████████░░░░░░░░ 60% (7/12)                │
│                                                 │
│  ✅ Model_A     → Snowflake (3.2s)             │
│  ✅ Model_B     → Snowflake (2.8s)             │
│  ✅ Model_C     → Fabric    (4.1s)             │
│  🔄 Model_D     → Snowflake (processing...)    │
│  🔄 Model_E     → Fabric    (processing...)    │
│  ⏳ Model_F     → pending                      │
│  ⏳ Model_G     → pending                      │
│                                                 │
│ Elapsed: 00:01:23  ETA: 00:00:55               │
└─────────────────────────────────────────────────┘
```

## Failure Strategies

### Best-Effort (Default)
- Continue processing remaining models even if some fail
- Collect all errors and report at the end
- Suitable for large batch operations

### Strict
- Stop on first failure
- Roll back completed work if possible
- Suitable for critical deployments

```yaml
# config.yaml
concurrency:
  failure_strategy: best-effort  # or "strict"
  max_workers: 4
  retry:
    max_retries: 3
    backoff_factor: 2.0
```

## Configuration

```yaml
# config.yaml
concurrency:
  enabled: true
  max_workers: null              # null = auto-detect
  parallel_threshold: 50         # Minimum measures for parallel processing
  max_worker_cap: 4              # Maximum workers regardless of CPU count
  failure_strategy: best-effort
  retry:
    max_retries: 3
    base_delay: 1.0
    max_delay: 60.0
    backoff_factor: 2.0
    jitter: true
```

## Integration with TieredSafetyClassifier

The concurrency engine integrates with the `TieredSafetyClassifier.classify_all()` method for parallel measure classification:

```python
results = classifier.classify_all(
    measures,
    max_workers=4,           # Worker threads
    progress_callback=cb     # Optional progress tracking
)
```

**Adaptive thresholds:**
- `_PARALLEL_THRESHOLD = 50`: Minimum measures to trigger parallelism
- `_MAX_WORKER_CAP = 4`: Maximum concurrent workers
- `max_workers=None` (default): Sequential processing

## Testing

```bash
# Run concurrency-specific tests
pytest tests/test_tiered_safety.py -v -k "parallel or concurrent"
pytest tests/test_customer_profitability_sync.py -v -k "broadcast"
```

## Related Documentation

- [Architecture](../Architecture.md)
- [Limitations](../Limitations.md)
- [Roadmap](../Roadmap.md)
