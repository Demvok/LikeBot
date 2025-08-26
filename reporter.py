import asyncio, uuid, signal, os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import motor.motor_asyncio
from pymongo import IndexModel
from pymongo import ASCENDING
from pymongo.write_concern import WriteConcern
from dotenv import load_dotenv
from logger import setup_logger

load_dotenv()

logger = setup_logger("reporter", "main.log")

# CONFIG
MONGO_URI = os.getenv("db_url", "mongodb://localhost:27017")
DB_NAME = os.getenv("db_name")
EVENTS_COLL = 'events'
RUNS_COLL = 'runs'

BATCH_SIZE = 100
BATCH_TIMEOUT = 0.5  # seconds


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

    async def event(self, run_id: str, task_id: str, level: str, code: str, message: str, payload: Optional[Dict[str, Any]] = None):
        item = {
            "type": "event",
            "run_id": run_id,
            "task_id": task_id,
            "ts": utc_now(),
            "level": level,
            "code": code,
            "message": message,
            "payload": payload or {}
        }
        await self.queue.put(item)



    # ---- Background writer ----
    async def _writer_loop(self):
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
                        print("Warning: insert_many failed:", e)
                        # fallback: try inserting one-by-one (so nothing is silently lost)
                        for doc in docs:
                            try:
                                await self.events_coll.insert_one(doc)
                            except Exception as ex:
                                # If even single insert fails, store minimal failure record in runs collection
                                await self.runs_coll.update_one({"run_id": doc["run_id"]}, {"$set": {"status": "persist_error"}})
                                print("Failed to persist doc:", ex)
                    buffer = []
                    last_flush = now

            except Exception as exc:
                # transient writer-level errors; avoid tight loop
                print("Writer loop error:", exc)
                await asyncio.sleep(0.2)



    async def start(self):
        await self.init()
        self._writer_task = asyncio.create_task(self._writer_loop())

    async def stop(self):
        # signal stop, wait for writer to flush remaining items
        self._stop_event.set()
        if self._writer_task:
            await self._writer_task
        # optionally close client
        self.client.close()

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
                    await self.reporter.event(self.run_id, self.task_id, "ERROR", "exception", str(exc), {"exc_type": str(exc_type)})
                    await self.reporter.end_run(self.run_id, status="failed", meta_patch={"error": str(exc)})
                else:
                    await self.reporter.end_run(self.run_id, status="success")
        return _RunCtx(self, task_id, meta)



# ---------------- Example usage ----------------

async def example_worker(task_id: str, reporter: Reporter):
    # using context manager
    async with await reporter.run_context(task_id, meta={"info": "demo"}) as run_id:
        await reporter.event(run_id, task_id, "INFO", "step.started", "Start step 1", {"step": 1})
        await asyncio.sleep(0.2)
        await reporter.event(run_id, task_id, "INFO", "step.progress", "50% done", {"progress": 50})
        # simulate error:
        # raise RuntimeError("Simulated")
        await reporter.event(run_id, task_id, "INFO", "step.finished", "Step 1 done", {"step": 1})
