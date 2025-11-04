import pandas as pd
import asyncio, uuid, os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from logger import setup_logger, load_config

load_dotenv()

logger = setup_logger("reporter", "main.log")
config = load_config()

# CONFIG
BATCH_SIZE = config.get('database', {}).get('batch_size', 100)
BATCH_TIMEOUT = config.get('database', {}).get('batch_timeout', 0.5)


def utc_now():
    return datetime.now(timezone.utc)


class Reporter:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def init(self):
        """Initialize database (ensure indexes are created)"""
        from database import get_db
        db = get_db()
        await db._ensure_ready()



    # ---- Public reporter API used by workers ----
    async def new_run(self, task_id: str, meta: Optional[Dict[str, Any]] = None) -> str:
        from database import get_db
        db = get_db()
        run_id = str(uuid.uuid4())
        # write run doc immediately for stronger traceability
        await db.create_run(run_id, task_id, meta)
        return run_id

    async def end_run(self, run_id: str, status: str = "success", meta_patch: Optional[Dict[str, Any]] = None):
        from database import get_db
        db = get_db()
        await db.end_run(run_id, status, meta_patch)

    async def event(self, run_id: str, task_id: str, level: str, code: str = None, message: str = None, payload: Optional[Dict[str, Any]] = None):
        """
        Asynchronously reports an event by adding it to the internal queue.
        Args:
            run_id (str): Unique identifier for the current run or session.
            task_id (str): Identifier for the specific task associated with the event.
            level (str): Severity level of the event (e.g., 'info', 'warning', 'error').
            code (str): Application-specific code representing the event type.
            message (str): Human-readable message describing the event.
            payload (Optional[Dict[str, Any]]): Additional data relevant to the event. Defaults to an empty dictionary if not provided.
        Raises:
            asyncio.QueueFull: If the queue is full and cannot accept new items.
        This method constructs an event dictionary with the provided details, including a timestamp, and enqueues it for further processing or reporting.
        """
        
        item = {
            "type": "event",
            "run_id": run_id,
            "task_id": task_id,
            "ts": utc_now(),
            "level": level,
            "code": code or None,
            "message": message,
            "payload": payload or {}
        }
        await self.queue.put(item)



    async def _writer_loop(self):
        """Background task for writing events to the database."""
        from database import get_db
        db = get_db()
        
        buffer: List[Dict[str, Any]] = []
        last_flush = asyncio.get_running_loop().time()

        while True:
            # stop condition: stop_event set and queue empty and buffer flushed
            if self._stop_event.is_set() and self.queue.empty() and not buffer:
                break

            try:
                try:
                    # wait for item or timeout
                    item = await asyncio.wait_for(self.queue.get(), timeout=BATCH_TIMEOUT)
                    buffer.append(item)
                    self.queue.task_done()
                except asyncio.TimeoutError:
                    item = None

                now = asyncio.get_running_loop().time()
                if buffer and (len(buffer) >= BATCH_SIZE or (now - last_flush) >= BATCH_TIMEOUT or (self._stop_event.is_set() and self.queue.empty())):
                    # prepare documents for insert
                    docs = []
                    for it in buffer:
                        # Normalize fields if necessary (Mongo can store datetimes directly)
                        docs.append({
                            "run_id": it["run_id"],
                            "task_id": it["task_id"],
                            "ts": it["ts"],
                            "level": it["level"],
                            "code": it["code"],
                            "message": it["message"],
                            "payload": it["payload"]
                        })
                    # Use centralized batch insert from database
                    try:
                        await db.create_events_batch(docs)
                    except Exception as e:
                        logger.warning("Batch insert failed:", e)
                    
                    buffer = []
                    last_flush = now

            except Exception as exc:
                # transient writer-level errors; avoid tight loop
                logger.error("Writer loop error:", exc)
                await asyncio.sleep(0.2)


    async def start(self):
        await self.init()
        self._writer_task = asyncio.create_task(self._writer_loop())

    async def stop(self):
        # signal stop, wait for writer to flush remaining items
        self._stop_event.set()
        if self._writer_task:
            await self._writer_task

    # Convenient context manager for a run
    async def run_context(self, task_id: str, meta: Optional[Dict[str, Any]] = None):
        """
        Async context manager usage:\n
        async with reporter.run_context("task-1") as run_id:
            await reporter.event(...)
        """
        # # graceful shutdown on signals
        # loop = asyncio.get_running_loop()
        # stop = asyncio.Event()
        # for sig in (signal.SIGINT, signal.SIGTERM):
        #     loop.add_signal_handler(sig, stop.set)
        class _RunCtx:
            def __init__(self, reporter, task_id, meta):
                self.reporter = reporter
                self.task_id = task_id
                self.meta = meta
                self.run_id = None
            async def __aenter__(self):
                self.run_id = await self.reporter.new_run(self.task_id, self.meta)
                return self.run_id
            async def __aexit__(self, exc_type, exc, tb):
                if exc:
                    await self.reporter.event(self.run_id, self.task_id, "ERROR", "error", f'Error: {exc}', {'exc': str(exc)})
                    await self.reporter.end_run(self.run_id, status="failed", meta_patch={"error": str(exc)})
                else:
                    await self.reporter.end_run(self.run_id, status="success")
        return _RunCtx(self, task_id, meta)


# Async class to manage runs and events data from MongoDB
class RunEventManager:
    def __init__(self):
        self.runs_df = pd.DataFrame()
        self.events_df = pd.DataFrame()

    async def refresh(self):
        """Reload data from MongoDB asynchronously."""
        from database import get_db
        db = get_db()
        
        # Get all runs and events using centralized methods
        runs = await db.get_all_runs()
        events = await db.get_all_events()
        
        self.runs_df = pd.DataFrame(runs)
        self.events_df = pd.DataFrame(events)

    async def get_tasks(self):
        """
        Returns a pandas DataFrame with all unique task_ids and the number of runs for each.
        Ensures columns are ordered: task_id, run_count.
        """
        from database import get_db
        db = get_db()
        
        results = await db.get_all_task_summaries()
        df = pd.DataFrame(results)
        # Ensure columns order
        df = df.loc[:, ["task_id", "run_count"]] if not df.empty else pd.DataFrame(columns=["task_id", "run_count"])
        return df

    async def get_runs(self, task_id) -> pd.DataFrame:
        """
        Return all runs for a given task_id, with an additional column 'event_count'
        indicating the number of events for each run, ordered by started_at descending.
        """
        from database import get_db
        db = get_db()
        
        # Get all runs for the task_id
        runs = await db.get_runs_by_task(task_id)
        if not runs:
            return pd.DataFrame(columns=["run_id", "task_id", "started_at", "finished_at", "status", "meta", "event_count"])

        # Get event counts for each run_id
        run_ids = [run["run_id"] for run in runs]
        event_count_map = await db.get_event_counts_for_runs(run_ids)

        # Add event_count to each run
        for run in runs:
            run["event_count"] = event_count_map.get(run["run_id"], 0)

        return pd.DataFrame(runs)

    async def get_task_details(self, task_id):
        """Return details for a single task."""
        from database import get_db
        db = get_db()
        
        runs = await db.get_runs_by_task(task_id)
        return runs

    async def get_events(self, run_id):
        """
        Return all events for a given run_id with details.
        Splits the 'code' field into 'event_type', 'action_type', and 'details' columns.
        Removes the original 'code' column.
        """
        from database import get_db
        db = get_db()
        
        events = await db.get_events_by_run(run_id)
        df = pd.DataFrame(events)
        if not df.empty and "code" in df.columns:
            split_cols = df["code"].str.split(".", expand=True)
            df["event_type"] = split_cols[0]
            df["action_type"] = split_cols[1] if split_cols.shape[1] > 1 else None
            df["details"] = split_cols[2] if split_cols.shape[1] > 2 else None
            df = df.drop(columns=["code"])
        return df

    async def get_event_details(self, event_id):
        """
        Return details for a single event.
        Splits the 'code' field into 'event_type', 'action_type', and 'details' columns.
        Removes the original 'code' field.
        """
        from database import get_db
        
        db = get_db()
        event = await db.get_event_by_id(event_id)
        
        if event:
            code = event.get("code", "")
            parts = code.split(".") if code else []
            event["event_type"] = parts[0] if len(parts) > 0 else None
            event["action_type"] = parts[1] if len(parts) > 1 else None
            event["details"] = parts[2] if len(parts) > 2 else None
            event.pop("code", None)
            return [event]
        return []

    async def delete_run(self, run_id):
        """Delete a run by run_id and all linked events."""
        from database import get_db
        db = get_db()
        
        result = await db.delete_run(run_id)
        await self.refresh()
        return result

    async def delete_event(self, event_id):
        """Delete an event by event_id."""
        from database import get_db
        
        db = get_db()
        result = await db.delete_event_by_id(event_id)
        await self.refresh()
        return result

    async def clear_runs(self, task_id):
        """Delete all runs for a given task_id and all linked events, then refresh data."""
        from database import get_db
        db = get_db()
        
        result = await db.clear_runs_by_task(task_id)
        await self.refresh()
        return result

async def create_report(data, type=None):
    """Create a report from the given data. As data standard uses data from get_events from RunEventManager. Report types are:
    - success: Report successful events
    - errors: Report error and warning events
    - full: Full report with all events
    - None: Raw data
    """
    from pandas import Series
    import asyncio
    # Drop columns if they exist (database may have already removed _id)
    preprocessed_data = data.drop(columns=['_id', 'task_id', 'run_id'], errors='ignore').reset_index(drop=True)
    preprocessed_data.ts = preprocessed_data.ts.dt.round('s')
    preprocessed_data.rename({'ts': 'datetime'}, axis=1, inplace=True)
    preprocessed_data = preprocessed_data.join(preprocessed_data['payload'].apply(Series)).drop(columns=['payload'], errors='ignore')

    async def gather_post_links(post_id: Series):
            async def fetch_post_link(post_id):
                from database import get_db
                db = get_db()
                post = await db.get_post(post_id) if post_id else None
                return post.message_link if post else None
            tasks = [fetch_post_link(post_id) for post_id in success_report['post_id']]
            return await asyncio.gather(*tasks)

    if type == 'success':
        success_report = preprocessed_data.loc[data['action_type'] == 'worker'].loc[preprocessed_data['event_type'] == 'info'].dropna(subset=['details']).drop(columns=['action_type', 'event_type', 'level'], errors='ignore')
        if 'post_id' in success_report.columns:
            success_report['post_id'] = await gather_post_links(success_report['post_id'])
            success_report.rename({'post_id': 'message_link'}, axis=1, inplace=True)
        return success_report.loc[success_report['details'] != 'action'].reset_index(drop=True)
    elif type == 'errors':
        return data.loc[data['event_type'] == 'error'].reset_index(drop=True).dropna(how='all', axis=1)
    elif type == 'full':
        return preprocessed_data
    else:
        return data  # Return the original data for unhandled types