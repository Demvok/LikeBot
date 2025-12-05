# v.1.1.3

## FIRST BUG

CRITICAL BUG: Task Status Logic Error
Issue: Task marked as CRASHED despite 2/3 workers succeeding

Root Cause: In task.py line 196, the _handle_worker_done() callback marks the entire task as CRASHED when any single worker raises an exception, even if other workers succeed.

Evidence from logs:

Problem: The _handle_worker_done() callback fires immediately when the first worker fails (at 16:54:41), marking the task CRASHED before the other 2 workers complete. This contradicts the later logic (lines 431-437) which correctly determines status should be FINISHED if any worker succeeded.

Impact: HIGH - Tasks incorrectly reported as failed when partially successful

## SECOND BUG

PRIMARY ISSUE: Username Case Sensitivity Race Condition
What Happened:

Post link contains UmanMVG (mixed case)
_normalize_url_identifier() converts it to lowercase: umanmvg
Account +380999041732 tries to resolve umanmvg → Telegram returns "No user has 'umanmvg' as username"
Account +18127322898 tries the SAME thing but succeeds (timing-dependent)
The successful resolution stores the alias, but the failed account had already started its attempts
Why Account +380999041732 Failed:

Cache is per-account (cache key = entity:+380999041732:umanmvg)
When +380999041732 started resolution, +18127322898 hadn't stored the DB alias yet
+380999041732 tried 5 different URL variations, all with lowercase umanmvg
Each attempt took 10 seconds to timeout (total 50 seconds wasted)
Meanwhile, +18127322898 succeeded and stored the alias in DB
Evidence:

```
16:53:11 - +380999041732 - DEBUG    - No channel found in DB for alias 'umanmvg'
16:53:11 - +380999041732 - Cache MISS: entity:+380999041732:umanmvg (fetching)
[... 5 failed attempts over 50 seconds ...]
16:54:01 - +18127322898 - Cache MISS: entity:+18127322898:umanmvg (fetching)
16:54:01 - DB   - INFO     - Adding url_alias 'umanmvg' to channel 2526968275
16:54:22 - +380999041732 - Cache MISS: entity:+380999041732:umanmvg (fetching) [AGAIN during worker phase]
```

### FIX 1
Investigate and fix client racing. Supposed place of racing violation - post validation, when post is correctly validated there is no need in second validation at the same time.


### FIX 2
No Early Termination on Username Errors
When +380999041732 fails with "username not found", the task continues
Other workers proceed to try the same (potentially invalid) username
Current behavior: Each worker independently discovers the error
Better approach: Share validation results across workers or fail fast on non-recoverable errors

### FIX 3
Workers Query DB Independently for Same Post
Each worker queries the same post from DB
Posts already loaded during validation phase
Fix: Pass Post objects directly to workers instead of links

### FIX 4
"Future exception was never retrieved" Warnings
```
Future exception was never retrieved
future: <Future finished exception=ValueError('No user has "umanmvg" as username')>
```
Cause: Exceptions raised in telegram_cache.py line 265 are stored in futures but never properly awaited
Impact: Clutters logs with 5 identical traceback warnings
Fix: Ensure all futures are properly awaited or add exception suppression