import asyncio
import pytest
from types import SimpleNamespace

# Ensure pytest-asyncio is available; tests use asyncio event loop

from main_logic.task import Task, WorkerResult
from main_logic.post import Post

class DummyDB:
    def __init__(self):
        self.updated = []
    async def _ensure_ready(self):
        # reporter.init expects this method; no-op for tests
        return
    async def create_run(self, run_id, task_id, meta=None):
        return run_id
    async def end_run(self, run_id, status: str = "success", meta_patch: dict = None):
        return True
    async def create_events_batch(self, events):
        return len(events)
    async def load_all_posts(self):
        # Return a simple post set used by the tests
        return [Post.from_keys("https://t.me/example/1", post_id=1)]
    async def update_task(self, task_id, update_data):
        # record calls
        self.updated.append((task_id, update_data))
        return True
    async def update_post(self, post_id, update_data):
        # tests don't need to persist posts; emulate success
        return True
    async def get_post(self, post_id):
        # Return a post object that appears validated (matching what Post.validate would produce)
        p = Post.from_keys("https://t.me/example/1", post_id=post_id)
        # mimic a validated post as validate() would set
        p.chat_id = 12345
        p.message_id = 1
        p.updated_at = p.updated_at  # keep recent timestamp
        return p

class DummyReporter:
    def __init__(self):
        self.events = []
    async def start(self):
        return
    async def stop(self):
        return
    async def run_context(self, task_id, meta=None):
        # Provide an async context manager that yields a run_id
        class Ctx:
            async def __aenter__(self_inner):
                return "run-1"
            async def __aexit__(self_inner, exc_type, exc, tb):
                # emulate reporter behaviour: if exc -> end run as failed; else success
                return False
        return Ctx()
    async def event(self, *args, **kwargs):
        self.events.append((args, kwargs))

class DummyClient:
    def __init__(self, account, phone_number=None):
        # account may be a dict-like or an account-like object returned by Account.get_accounts
        if isinstance(account, dict):
            self.account = SimpleNamespace(account_id=account.get('account_id'), phone_number=account.get('phone_number'))
            self.account_id = self.account.account_id
            self.phone_number = self.account.phone_number
        else:
            # assume account-like object with attributes
            self.account = account
            self.account_id = getattr(account, 'account_id', None)
            self.phone_number = getattr(account, 'phone_number', None)
        # minimal client fields
        self._task_id = None
        self._is_locked = False
    async def react(self, message_link=None):
        # real clients would do network IO; test will monkeypatch
        return True
    async def connect(self, task_id=None):
        self._task_id = task_id
        return True
    async def disconnect(self):
        return True
    async def get_message_ids(self, message_link):
        # Return deterministic chat_id/message_id/entity tuple for tests
        return (12345, 1, None)

class DummyClientFactory:
    # Minimal Client.connect_clients and disconnect
    @staticmethod
    async def connect_clients(accounts, logger=None, task_id=None):
        # create DummyClient per account
        out = []
        for idx, acc in enumerate(accounts):
            # Pass the account through to DummyClient; DummyClient will normalize dict or object
            client = DummyClient(acc)
            await client.connect(task_id=task_id)
            out.append(client)
        return out
    @staticmethod
    async def disconnect_clients(clients, logger=None, task_id=None):
        if not clients:
            return None
        for c in clients:
            await c.disconnect()
        return None

@pytest.fixture(autouse=True)
def patch_environment(monkeypatch):
    # Patch database.get_db to return DummyDB
    import main_logic.database as database
    monkeypatch.setattr(database, 'get_db', lambda: DummyDB())
    # Patch reporter.Reporter to return DummyReporter
    import auxilary_logic.reporter as reporter
    monkeypatch.setattr(reporter, 'Reporter', lambda : DummyReporter())
    # Patch Client factory in task imports
    import main_logic.task as task_module
    monkeypatch.setattr(task_module, 'Client', DummyClientFactory)
    # Make delays small so workers run fast in tests
    task_module.config['delays'] = {
        'worker_start_delay_min': 0.0,
        'worker_start_delay_max': 0.0,
        'min_delay_between_reactions': 0.0,
        'max_delay_between_reactions': 0.0,
        'action_retries': 1,
        'action_retry_delay': 0
    }
    # Patch Account.get_accounts to return accounts as list of dicts matching DummyClientFactory expectations
    import main_logic.agent as agent
    async def fake_get_accounts(cls, phones: list):
        out = []
        # Lightweight account-like object with is_usable method
        class SimpleAccount:
            def __init__(self, account_id, phone_number):
                self.account_id = account_id
                self.phone_number = phone_number
                self.status = None
            def is_usable(self):
                return True
        for idx, p in enumerate(phones):
            if isinstance(p, dict):
                phone = p.get('phone_number', f'+000{idx}')
                acc_id = p.get('account_id', f'acc{idx}')
                out.append(SimpleAccount(acc_id, phone))
            else:
                out.append(SimpleAccount(str(p), str(p)))
        return out
    monkeypatch.setattr(agent.Account, 'get_accounts', classmethod(fake_get_accounts))
    yield

@pytest.mark.asyncio
async def test_single_worker_failure_does_not_crash_task(monkeypatch):
    # Prepare a Task with 2 accounts; make first worker raise, second succeed
    # Make Post list with one post
    posts = [Post.from_keys("https://t.me/example/1", post_id=1)]

    # Create Task instance
    t = Task(name="t1", post_ids=[1], accounts=[{"account_id": "acc1"}, {"account_id": "acc2"}], action={"type": "react"}, task_id=42)

    # Patch client.react to raise for first client only
    original_react = DummyClient.react
    async def react_raise_once(self, message_link=None):
        if self.account_id == 'acc1':
            raise RuntimeError("worker failure")
        return await original_react(self, message_link=message_link)
    monkeypatch.setattr(DummyClient, 'react', react_raise_once)

    # Run the task and wait for completion (run_and_wait will start the task)
    await t.run_and_wait()

    # After run: if at least one worker succeeded, the task should be FINISHED
    assert t.status == Task.TaskStatus.FINISHED

@pytest.mark.asyncio
async def test_all_workers_failure_marks_crashed(monkeypatch):
    # Prepare Task with 2 accounts; make both workers raise unhandled exceptions
    posts = [Post.from_keys("https://t.me/example/1", post_id=1)]
    t = Task(name="t2", post_ids=[1], accounts=[{"account_id": "accA"}, {"account_id": "accB"}], action={"type": "react"}, task_id=43)

    # Make the worker coroutine itself raise so the worker task ends with an unhandled exception
    async def failing_client_worker(self, client, posts, reporter, run_id):
        raise RuntimeError("worker failure always")
    monkeypatch.setattr(Task, 'client_worker', failing_client_worker)

    # Run the task and wait for completion
    await t.run_and_wait()

    # When all workers throw unhandled exceptions, Task should be CRASHED
    assert t.status == Task.TaskStatus.CRASHED

@pytest.mark.asyncio
async def test_all_workers_account_failure_marks_failed(monkeypatch):
    """Test that when all workers fail due to account issues (not exceptions),
    the task status is set to FAILED instead of CRASHED."""
    posts = [Post.from_keys("https://t.me/example/1", post_id=1)]
    t = Task(name="t3", post_ids=[1], accounts=[{"account_id": "accX", "phone_number": "+1111"}, {"account_id": "accY", "phone_number": "+2222"}], action={"type": "react"}, task_id=44)

    # Make workers return WorkerResult with account_issue failure
    async def account_issue_worker(self, client, posts, reporter, run_id):
        # Simulate account issue - worker returns normally but indicates failure
        return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue')
    monkeypatch.setattr(Task, 'client_worker', account_issue_worker)

    # Run the task and wait for completion
    await t.run_and_wait()

    # When all workers fail due to account issues, Task should be FAILED (not CRASHED)
    assert t.status == Task.TaskStatus.FAILED

@pytest.mark.asyncio
async def test_mixed_worker_results_with_one_success_marks_finished(monkeypatch):
    """Test that if at least one worker succeeds, task is FINISHED even if others fail."""
    posts = [Post.from_keys("https://t.me/example/1", post_id=1)]
    t = Task(name="t4", post_ids=[1], accounts=[{"account_id": "accP", "phone_number": "+3333"}, {"account_id": "accQ", "phone_number": "+4444"}], action={"type": "react"}, task_id=45)

    call_count = [0]
    
    # First worker fails with account issue, second succeeds
    async def mixed_result_worker(self, client, posts, reporter, run_id):
        call_count[0] += 1
        if call_count[0] == 1:
            # First worker - account issue
            return WorkerResult(success=False, phone_number=client.phone_number, failure_reason='account_issue')
        else:
            # Second worker - success
            return WorkerResult(success=True, phone_number=client.phone_number)
    monkeypatch.setattr(Task, 'client_worker', mixed_result_worker)

    # Run the task and wait for completion
    await t.run_and_wait()

    # With at least one success, task should be FINISHED
    assert t.status == Task.TaskStatus.FINISHED
