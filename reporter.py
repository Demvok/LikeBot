import asyncio
import aiosqlite
import uuid
import json
import os
import time
import signal
from datetime import datetime

# TEMPORARY UNUSABLE


DB_PATH = "task_reports.db"
JSONL_DIR = "task_reports_jsonl"
BATCH_SIZE = 50
BATCH_TIMEOUT = 0.5  # сек

os.makedirs(JSONL_DIR, exist_ok=True)

def now_iso():
    return datetime.utcnow().isoformat() + "Z"

async def init_db(db_path=DB_PATH):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=FULL;")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS task_runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            status TEXT,
            meta JSON
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            task_id TEXT,
            ts TEXT,
            level TEXT,      -- INFO/ERROR/DEBUG
            code TEXT,       -- machine-readable code
            message TEXT,
            payload JSON
        );
        """)
        await db.commit()

class TaskReporter:
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue

    async def new_run(self, task_id: str, meta: dict | None = None):
        run_id = str(uuid.uuid4())
        await self.queue.put({
            "type": "run_start",
            "run_id": run_id,
            "task_id": task_id,
            "ts": now_iso(),
            "meta": meta or {}
        })
        return run_id

    async def end_run(self, run_id: str, task_id: str, status: str, meta: dict | None = None):
        await self.queue.put({
            "type": "run_end",
            "run_id": run_id,
            "task_id": task_id,
            "ts": now_iso(),
            "status": status,
            "meta": meta or {}
        })

    async def event(self, run_id: str, task_id: str, level: str, code: str, message: str, payload: dict | None = None):
        await self.queue.put({
            "type": "event",
            "run_id": run_id,
            "task_id": task_id,
            "ts": now_iso(),
            "level": level,
            "code": code,
            "message": message,
            "payload": payload or {}
        })

async def writer_loop(queue: asyncio.Queue, db_path=DB_PATH, jsonl_dir=JSONL_DIR, stop_event: asyncio.Event = None):
    """
    Беремо записи з черги, записуємо батчами в SQLite та в JSONL.
    """
    await init_db(db_path)
    jsonl_files = {}  # run_id -> open file handle
    async with aiosqlite.connect(db_path) as db:
        # великий цикл
        buffer = []
        last_flush = time.time()

        async def flush_buffer():
            nonlocal buffer, db
            if not buffer:
                return
            # запис у DB в транзакції
            async with db.execute("BEGIN"):
                for item in buffer:
                    if item["type"] == "run_start":
                        await db.execute(
                            "INSERT OR REPLACE INTO task_runs (run_id, task_id, started_at, status, meta) VALUES (?, ?, ?, ?, ?)",
                            (item["run_id"], item["task_id"], item["ts"], "running", json.dumps(item["meta"]))
                        )
                    elif item["type"] == "run_end":
                        await db.execute(
                            "UPDATE task_runs SET finished_at = ?, status = ?, meta = json_patch(meta, ?) WHERE run_id = ?",
                            (item["ts"], item["status"], json.dumps(item["meta"]), item["run_id"])
                        )
                    elif item["type"] == "event":
                        await db.execute(
                            "INSERT INTO events (run_id, task_id, ts, level, code, message, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (item["run_id"], item["task_id"], item["ts"], item["level"], item["code"], item["message"], json.dumps(item["payload"]))
                        )
            await db.commit()

            # JSONL mirror
            for item in buffer:
                run_file = os.path.join(jsonl_dir, f"{item['run_id']}.jsonl")
                # append line atomically
                line = json.dumps(item, ensure_ascii=False) + "\n"
                # open/append and fsync
                with open(run_file, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())

            buffer = []

        while True:
            try:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=BATCH_TIMEOUT)
                except asyncio.TimeoutError:
                    item = None

                if item is not None:
                    buffer.append(item)
                    queue.task_done()

                # Накопичили достатньо або таймаут/зовнішній stop
                if (len(buffer) >= BATCH_SIZE) or (time.time() - last_flush >= BATCH_TIMEOUT) or (stop_event and stop_event.is_set() and buffer):
                    await flush_buffer()
                    last_flush = time.time()

                if stop_event and stop_event.is_set():
                    # перед виходом — переконатися, що черга пуста
                    # Drain queue quickly
                    while True:
                        try:
                            item = queue.get_nowait()
                            buffer.append(item)
                            queue.task_done()
                            if len(buffer) >= BATCH_SIZE:
                                await flush_buffer()
                        except asyncio.QueueEmpty:
                            break
                    if buffer:
                        await flush_buffer()
                    break

            except Exception as exc:
                # У реальному коді — використовуй backoff, логування в stderr та retry
                print("Writer error:", exc)
                await asyncio.sleep(0.2)

async def example_worker(task_id: str, reporter: TaskReporter):
    run_id = await reporter.new_run(task_id, meta={"info": "demo"})
    try:
        # симуляція подій
        await reporter.event(run_id, task_id, "INFO", "step.started", "Start step 1", {"step": 1})
        await asyncio.sleep(0.1)
        await reporter.event(run_id, task_id, "INFO", "step.progress", "Progress 50%", {"progress": 50})
        # проблема
        # raise ValueError("Simulated failure")
        await reporter.event(run_id, task_id, "INFO", "step.finished", "Step 1 done", {"step": 1})
        await reporter.end_run(run_id, task_id, status="success")
    except Exception as e:
        await reporter.event(run_id, task_id, "ERROR", "exception", str(e), {"exc_type": type(e).__name__})
        await reporter.end_run(run_id, task_id, status="failed", meta={"error": str(e)})

async def main():
    queue = asyncio.Queue()
    reporter = TaskReporter(queue)
    stop_event = asyncio.Event()

    # починаємо writer
    writer_task = asyncio.create_task(writer_loop(queue, stop_event=stop_event))

    # налаштування graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # запускаємо кілька робіт
    workers = [asyncio.create_task(example_worker(f"task-{i}", reporter)) for i in range(5)]
    await asyncio.gather(*workers)
    # все готово — ставимо stop
    stop_event.set()
    await writer_task