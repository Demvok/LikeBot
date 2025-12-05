import logging
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import ensure_proxies_exist, app
from main_logic.client_mixins.proxy import ProxyMixin
from main_logic.database import MongoStorage
from auxilary_logic.auth import get_current_user


class _DummyProxyClient(ProxyMixin):
    def __init__(self, assigned):
        self.account = type('Account', (), {'assigned_proxies': assigned})()
        self.phone_number = '+19998887777'
        self.logger = logging.getLogger('proxy-test')
        self.proxy_name = None


class _FakeDB:
    def __init__(self, proxies):
        self._proxies = proxies

    async def get_proxy(self, proxy_name):
        return self._proxies.get(proxy_name)


@pytest.mark.asyncio
async def test_proxy_mixin_uses_account_assigned_proxy(monkeypatch):
    fake_proxy = {
        'proxy_name': 'alpha-proxy',
        'host': '1.2.3.4',
        'port': 1080,
        'type': 'socks5',
        'active': True,
    }
    fake_db = _FakeDB({'alpha-proxy': fake_proxy})

    monkeypatch.setattr('main_logic.database.get_db', lambda: fake_db)
    monkeypatch.setattr('auxilary_logic.proxy.build_proxy_candidates', lambda data, logger: [{'addr': data['host'], 'port': data['port'], 'proxy_type': data.get('type')}])
    monkeypatch.setattr('random.shuffle', lambda seq: None)

    client = _DummyProxyClient(['alpha-proxy'])
    candidates, proxy_data = await client._get_proxy_config('soft')

    assert proxy_data == fake_proxy
    assert candidates and candidates[0]['addr'] == '1.2.3.4'
    assert client.proxy_name == 'alpha-proxy'


@pytest.mark.asyncio
async def test_proxy_mixin_strict_mode_without_assignments():
    client = _DummyProxyClient([])
    with pytest.raises(RuntimeError):
        await client._get_proxy_config('strict')


@pytest.mark.asyncio
async def test_proxy_mixin_soft_mode_without_assignments():
    client = _DummyProxyClient([])
    candidates, proxy_data = await client._get_proxy_config('soft')
    assert candidates is None and proxy_data is None


@pytest.mark.asyncio
async def test_ensure_proxies_exist_validates_active(monkeypatch):
    class _EnsureFakeDB:
        def __init__(self):
            self.calls = []

        async def get_proxy(self, proxy_name):
            self.calls.append(proxy_name)
            proxies = {
                'alpha-proxy': {'proxy_name': 'alpha-proxy', 'active': True},
                'beta-proxy': {'proxy_name': 'beta-proxy', 'active': False},
            }
            return proxies.get(proxy_name)

    db_instance = _EnsureFakeDB()
    monkeypatch.setattr('main.get_db', lambda: db_instance)

    result = await ensure_proxies_exist(['alpha-proxy'])
    assert result == ['alpha-proxy']

    with pytest.raises(HTTPException) as exc:
        await ensure_proxies_exist(['beta-proxy'])
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await ensure_proxies_exist(['missing-proxy'])
    assert exc.value.status_code == 404


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs
        self._index = 0

    def sort(self, field, direction):
        reverse = direction == -1
        self._docs = sorted(self._docs, key=lambda item: item.get(field, 0), reverse=reverse)
        self._index = 0
        return self

    def limit(self, _):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._docs):
            raise StopAsyncIteration
        item = self._docs[self._index]
        self._index += 1
        return item


class _FakeProxyStore:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query):
        excluded = set(query.get('proxy_name', {}).get('$nin', []))
        active_only = query.get('active')
        filtered = []
        for doc in self._docs:
            if doc['proxy_name'] in excluded:
                continue
            if active_only is True and not doc.get('active', False):
                continue
            filtered.append(doc.copy())
        return _FakeCursor(filtered)


class _FakeAccountStore:
    def __init__(self, assigned):
        self.assigned = assigned

    async def find_one(self, *_args, **_kwargs):
        return {'assigned_proxies': list(self.assigned)}


class _EmptyProjectionAccountStore:
    def __init__(self):
        self.calls = 0
        self.assigned = []

    async def find_one(self, *_args, **_kwargs):
        self.calls += 1
        if self.calls == 1:
            return {}
        return {'assigned_proxies': list(self.assigned)}


@pytest.mark.asyncio
async def test_auto_assign_proxies_prefers_least_linked(monkeypatch):
    accounts = _FakeAccountStore(['alpha'])
    proxies = _FakeProxyStore([
        {'proxy_name': 'beta', 'active': True, 'linked_accounts_count': 0},
        {'proxy_name': 'gamma', 'active': True, 'linked_accounts_count': 2},
        {'proxy_name': 'delta', 'active': True, 'linked_accounts_count': 1},
    ])

    async def fake_link(_phone, proxy_name):
        if proxy_name in accounts.assigned:
            return False
        accounts.assigned.append(proxy_name)
        return True

    async def fake_ready():
        return None

    monkeypatch.setattr(MongoStorage, '_accounts', accounts)
    monkeypatch.setattr(MongoStorage, '_proxies', proxies)
    monkeypatch.setattr(MongoStorage, '_ensure_ready', fake_ready)
    monkeypatch.setattr(MongoStorage, 'link_proxy_to_account', fake_link)

    result = await MongoStorage.auto_assign_proxies('+123', desired_count=3)

    assert result['added'] == ['beta', 'delta']
    assert result['assigned_proxies'] == ['alpha', 'beta', 'delta']
    assert result['remaining'] == 0


@pytest.mark.asyncio
async def test_auto_assign_reports_shortfall(monkeypatch):
    accounts = _FakeAccountStore([])
    proxies = _FakeProxyStore([
        {'proxy_name': 'lonely', 'active': True, 'linked_accounts_count': 5},
    ])

    async def fake_link(_phone, proxy_name):
        if proxy_name in accounts.assigned:
            return False
        accounts.assigned.append(proxy_name)
        return True

    async def fake_ready():
        return None

    monkeypatch.setattr(MongoStorage, '_accounts', accounts)
    monkeypatch.setattr(MongoStorage, '_proxies', proxies)
    monkeypatch.setattr(MongoStorage, '_ensure_ready', fake_ready)
    monkeypatch.setattr(MongoStorage, 'link_proxy_to_account', fake_link)

    result = await MongoStorage.auto_assign_proxies('+456', desired_count=4)

    assert result['added'] == ['lonely']
    assert result['remaining'] == 3
    assert 'message' in result and 'Not enough eligible proxies' in result['message']


def test_auto_assign_route_takes_precedence(monkeypatch):
    class _AutoAssignStub:
        def __init__(self):
            self.auto_calls = 0

        async def auto_assign_proxies(self, phone_number, desired_count=None, active_only=True):
            self.auto_calls += 1
            return {
                'phone_number': phone_number,
                'target': desired_count or 2,
                'assigned_proxies': [],
                'added': [],
                'remaining': 0,
            }

        async def link_proxy_to_account(self, *_args, **_kwargs):
            raise AssertionError("link_proxy_to_account should not handle auto-assign route")

    stub_db = _AutoAssignStub()
    monkeypatch.setattr('main.get_db', lambda: stub_db)

    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: {'username': 'tester'}
    try:
        response = client.post('/accounts/%2B777/proxies/auto-assign')
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200, response.text
    assert stub_db.auto_calls == 1


@pytest.mark.asyncio
async def test_auto_assign_does_not_fail_when_projection_is_empty(monkeypatch):
    accounts = _EmptyProjectionAccountStore()
    proxies = _FakeProxyStore([
        {'proxy_name': 'only-one', 'active': True, 'linked_accounts_count': 0},
    ])

    async def fake_ready():
        return None

    async def fake_link(_phone, proxy_name):
        accounts.assigned.append(proxy_name)
        return True

    monkeypatch.setattr(MongoStorage, '_accounts', accounts)
    monkeypatch.setattr(MongoStorage, '_proxies', proxies)
    monkeypatch.setattr(MongoStorage, '_ensure_ready', fake_ready)
    monkeypatch.setattr(MongoStorage, 'link_proxy_to_account', fake_link)

    result = await MongoStorage.auto_assign_proxies('+111', desired_count=1)

    assert result['added'] == ['only-one']
    assert accounts.calls == 2  # initial lookup + refresh