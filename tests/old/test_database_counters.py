import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from main_logic.database import MongoStorage, ensure_async
from pymongo.errors import DuplicateKeyError

# --- helpers: fake async collection implementations ---
class FakeCounters:
    def __init__(self):
        self.store = {}

    async def find_one_and_update(self, filter_doc, update_doc, upsert=False, return_document=None):
        key = filter_doc.get('_id')
        if key not in self.store:
            # initialize seq
            self.store[key] = {'_id': key, 'seq': 0}
        # apply $inc
        inc = update_doc.get('$inc', {})
        for k, v in inc.items():
            self.store[key][k] = self.store[key].get(k, 0) + v
        return self.store[key]

    async def create_index(self, spec, unique=False):
        # no-op for tests
        return None

class FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, filter_doc):
        # simple equality on post_id/task_id
        for d in self.docs.values():
            match = True
            for k, v in filter_doc.items():
                if d.get(k) != v:
                    match = False
                    break
            if match:
                return d
        return None

    async def insert_one(self, doc):
        key = doc.get('post_id') or doc.get('task_id')
        if key in self.docs:
            raise DuplicateKeyError('duplicate')
        # store a shallow copy
        self.docs[key] = dict(doc)
        # motor insert_one returns a InsertOneResult but we don't need it here
        return None

    async def create_index(self, *args, **kwargs):
        return None

# --- tests ---

def test_ensure_async_uses_to_thread():
    # Create a dummy sync function
    def blocking_fn(x, y=0):
        return x + y

    wrapped = ensure_async(blocking_fn)

    called = {}

    async def fake_to_thread(fn, *args, **kwargs):
        # verify that the function passed is the original blocking function
        called['fn'] = fn
        called['args'] = args
        called['kwargs'] = kwargs
        return fn(*args, **kwargs)

    async def run():
        with patch('asyncio.to_thread', new=fake_to_thread):
            result = await wrapped(2, y=3)
            assert result == 5
            assert called['fn'] is blocking_fn
            assert called['args'] == (2,)
            assert called['kwargs'] == {'y': 3}

    asyncio.get_event_loop().run_until_complete(run())


@pytest.mark.asyncio
async def test_add_post_allocates_counter_and_inserts(monkeypatch):
    # Prepare fake collections
    counters = FakeCounters()
    posts = FakeCollection()

    # Monkeypatch _ensure_ready to noop so it doesn't touch real DB
    async def noop_ensure_ready(cls):
        return None

    monkeypatch.setattr(MongoStorage, '_ensure_ready', classmethod(noop_ensure_ready))

    # Set fake collections on class
    MongoStorage._counters = counters
    MongoStorage._posts = posts

    # Prepare a simple post dict without post_id
    post = {'message_link': 'http://t.me/test/1', 'content': 'hello'}

    # Call add_post
    res = await MongoStorage.add_post(post)
    assert res is True
    # Ensure a post_id was assigned and stored
    assert 'post_id' in post
    stored = await posts.find_one({'post_id': post['post_id']})
    assert stored is not None
    assert stored['message_link'] == 'http://t.me/test/1'


@pytest.mark.asyncio
async def test_add_task_allocates_counter_and_inserts(monkeypatch):
    counters = FakeCounters()
    tasks = FakeCollection()

    async def noop_ensure_ready(cls):
        return None

    monkeypatch.setattr(MongoStorage, '_ensure_ready', classmethod(noop_ensure_ready))

    MongoStorage._counters = counters
    MongoStorage._tasks = tasks

    task = {'name': 'do-something', 'post_ids': []}

    res = await MongoStorage.add_task(task)
    assert res is True
    assert 'task_id' in task
    stored = await tasks.find_one({'task_id': task['task_id']})
    assert stored is not None
    assert stored['name'] == 'do-something'
