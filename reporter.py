import pandas as pd
import asyncio, uuid, signal, os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import motor.motor_asyncio
from pymongo import IndexModel
from pymongo import ASCENDING
from pymongo.write_concern import WriteConcern
from dotenv import load_dotenv
from logger import setup_logger, load_config

load_dotenv()

logger = setup_logger("reporter", "main.log")
config = load_config()

# CONFIG
MONGO_URI = os.getenv("db_url", "mongodb://localhost:27017")
DB_NAME = os.getenv("db_name")
EVENTS_COLL = config.get('database', {}).get('events_coll', 'events')
RUNS_COLL = config.get('database', {}).get('runs_coll', 'runs')

BATCH_SIZE = config.get('database', {}).get('batch_size', 100)
BATCH_TIMEOUT = config.get('database', {}).get('batch_timeout', 0.5)


def utc_now():
    return datetime.now(timezone.utc)


class Reporter:
    def __init__(self):
        self.client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        self.db = self.client[DB_NAME]

        # Use majority write concern + journaling for durability guarantees (requires replica set for true majority)
        self.events_coll = self.db.get_collection(EVENTS_COLL, write_concern=WriteConcern(w="majority", j=True))
        self.runs_coll = self.db.get_collection(RUNS_COLL, write_concern=WriteConcern(w="majority", j=True))

        self.queue: asyncio.Queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    async def init(self):
        """Create indexes (idempotent)"""
        await self.runs_coll.create_index([("run_id", ASCENDING)], unique=True)
        # Index events by run_id, ts for fast querying by run
        await self.events_coll.create_indexes([
            IndexModel([("run_id", ASCENDING), ("ts", ASCENDING)]),
            IndexModel([("task_id", ASCENDING)]),
            IndexModel([("level", ASCENDING)]),
            IndexModel([("code", ASCENDING)])
        ])



    # ---- Public reporter API used by workers ----
    async def new_run(self, task_id: str, meta: Optional[Dict[str, Any]] = None) -> str:
        run_id = str(uuid.uuid4())
        doc = {
            "run_id": run_id,
            "task_id": task_id,
            "started_at": utc_now(),
            "finished_at": None,
            "status": "running",
            "meta": meta or {}
        }
        # write run doc immediately for stronger traceability
        await self.runs_coll.insert_one(doc)
        return run_id

    async def end_run(self, run_id: str, status: str = "success", meta_patch: Optional[Dict[str, Any]] = None):
        update = {"$set": {"finished_at": utc_now(), "status": status}}
        if meta_patch:
            update["$set"]["meta"] = meta_patch
        await self.runs_coll.update_one({"run_id": run_id}, update)

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
                    # unordered insert for speed and resilience; write concern handled by collection's WriteConcern
                    try:
                        await self.events_coll.insert_many(docs, ordered=False)
                    except Exception as e:
                        # handle partial failures — you might want to log this to stderr or another collection
                        logger.warning("Warning: insert_many failed:", e)
                        # fallback: try inserting one-by-one (so nothing is silently lost)
                        for doc in docs:
                            try:
                                await self.events_coll.insert_one(doc)
                            except Exception as ex:
                                # If even single insert fails, store minimal failure record in runs collection
                                await self.runs_coll.update_one({"run_id": doc["run_id"]}, {"$set": {"status": "persist_error"}})
                                logger.error("Failed to persist doc:", ex)
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
        self.client.close()  # optionally close client

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
        self.client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        self.db = self.client[DB_NAME]
        self.runs_coll = self.db[RUNS_COLL]
        self.events_coll = self.db[EVENTS_COLL]
        self.runs_df = pd.DataFrame()
        self.events_df = pd.DataFrame()

    async def refresh(self):
        """Reload data from MongoDB asynchronously."""
        runs_cursor = self.runs_coll.find()
        events_cursor = self.events_coll.find()
        runs = await runs_cursor.to_list(length=None)
        events = await events_cursor.to_list(length=None)
        self.runs_df = pd.DataFrame(runs)
        self.events_df = pd.DataFrame(events)

    async def get_tasks(self):
        """
        Returns a pandas DataFrame with all unique task_ids and the number of runs for each.
        Ensures columns are ordered: task_id, run_count.
        """
        pipeline = [
            {"$group": {"_id": "$task_id", "run_count": {"$sum": 1}}},
            {"$project": {"task_id": "$_id", "run_count": 1, "_id": 0}}
        ]
        cursor = self.runs_coll.aggregate(pipeline)
        results = await cursor.to_list(length=None)
        df = pd.DataFrame(results)
        # Ensure columns order
        df = df.loc[:, ["task_id", "run_count"]] if not df.empty else pd.DataFrame(columns=["task_id", "run_count"])
        return df

    async def get_runs(self, task_id) -> pd.DataFrame:
        """
        Return all runs for a given task_id, with an additional column 'event_count'
        indicating the number of events for each run, ordered by started_at descending.
        """
        # Get all runs for the task_id, ordered by started_at descending
        cursor = self.runs_coll.find({'task_id': task_id}).sort('started_at', -1)
        runs = await cursor.to_list(length=None)
        if not runs:
            return pd.DataFrame(columns=["run_id", "task_id", "started_at", "finished_at", "status", "meta", "event_count"])

        # Get event counts for each run_id
        run_ids = [run["run_id"] for run in runs]
        pipeline = [
            {"$match": {"run_id": {"$in": run_ids}}},
            {"$group": {"_id": "$run_id", "event_count": {"$sum": 1}}}
        ]
        event_counts_cursor = self.events_coll.aggregate(pipeline)
        event_counts = await event_counts_cursor.to_list(length=None)
        event_count_map = {ec["_id"]: ec["event_count"] for ec in event_counts}

        # Add event_count to each run
        for run in runs:
            run["event_count"] = event_count_map.get(run["run_id"], 0)

        return pd.DataFrame(runs)

    async def get_task_details(self, task_id):
        """Return details for a single task."""
        cursor = self.runs_coll.find({'task_id': task_id})
        runs = await cursor.to_list(length=None)
        return runs

    async def get_events(self, run_id):
        """
        Return all events for a given run_id with details.
        Splits the 'code' field into 'event_type', 'action_type', and 'details' columns.
        Removes the original 'code' column.
        """
        cursor = self.events_coll.find({'run_id': run_id}).sort('ts', 1)
        events = await cursor.to_list(length=None)
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
        from bson import ObjectId
        cursor = self.events_coll.find({'_id': ObjectId(event_id)})
        events = await cursor.to_list(length=None)
        if events:
            for event in events:
                code = event.get("code", "")
                parts = code.split(".") if code else []
                event["event_type"] = parts[0] if len(parts) > 0 else None
                event["action_type"] = parts[1] if len(parts) > 1 else None
                event["details"] = parts[2] if len(parts) > 2 else None
                event.pop("code", None)
        return events

    async def delete_run(self, run_id):
        """Delete a run by run_id and all linked events."""
        run_result = await self.runs_coll.delete_one({'run_id': run_id})
        event_result = await self.events_coll.delete_many({'run_id': run_id})
        await self.refresh()
        return {'runs_deleted': run_result.deleted_count, 'events_deleted': event_result.deleted_count}

    async def delete_event(self, event_id):
        """Delete an event by event_id."""
        result = await self.events_coll.delete_one({'event_id': event_id})
        await self.refresh()
        return result.deleted_count

    async def clear_runs(self, task_id):
        """Delete all runs for a given task_id and all linked events, then refresh data."""
        # Find all run_ids for the given task_id
        runs_cursor = self.runs_coll.find({'task_id': task_id}, {'run_id': 1})
        runs = await runs_cursor.to_list(length=None)
        run_ids = [run['run_id'] for run in runs]

        # Delete runs
        runs_result = await self.runs_coll.delete_many({'task_id': task_id})

        # Delete linked events
        if run_ids:
            events_result = await self.events_coll.delete_many({'run_id': {'$in': run_ids}})
            events_deleted = events_result.deleted_count
        else:
            events_deleted = 0

        await self.refresh()
        return {'runs_deleted': runs_result.deleted_count, 'events_deleted': events_deleted}

async def create_report(data, type=None):
    """Create a report from the given data. As data standard uses data from get_events from RunEventManager. Report types are:
    - success: Report successful events
    - errors: Report error and warning events
    """
    from pandas import Series
    import asyncio
    preprocessed_data = data.drop(['_id', 'task_id', 'run_id'], axis=1).reset_index(drop=True)
    preprocessed_data.ts = preprocessed_data.ts.dt.round('s')
    preprocessed_data.rename({'ts': 'datetime'}, axis=1, inplace=True)
    preprocessed_data = preprocessed_data.join(preprocessed_data['payload'].apply(Series)).drop('payload', axis=1)

    async def gather_post_links(post_id: Series):
            async def fetch_post_link(post_id):
                from database import get_db
                db = get_db()
                post = await db.get_post(post_id) if post_id else None
                return post.message_link if post else None
            tasks = [fetch_post_link(post_id) for post_id in success_report['post_id']]
            return await asyncio.gather(*tasks)

    if type == 'success':
        success_report = preprocessed_data.loc[data['action_type'] == 'worker'].loc[preprocessed_data['event_type'] == 'info'].dropna(subset=['details']).drop(['action_type', 'event_type', 'level'], axis=1)
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