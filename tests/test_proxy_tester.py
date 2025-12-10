import pytest

from utils import proxy_tester


class _DummyResponse:
    def __init__(self, *, json_payload=None, text='', status_code=200):
        self._json_payload = json_payload
        self.text = text
        self.status_code = status_code

    def json(self):
        if self._json_payload is None:
            raise ValueError("No JSON payload")
        return self._json_payload


def _sample_proxy(**overrides):
    data = {
        'proxy_name': 'alpha-proxy',
        'host': '127.0.0.1',
        'socks5_port': 9050,
        'active': True,
        'username': 'user',
        'password': 'pass',
        'rdns': True,
    }
    data.update(overrides)
    return data


def test_build_endpoint_uses_socks5h_when_rnds_enabled():
    proxy = _sample_proxy(rdns=True)
    endpoints = proxy_tester._build_endpoint_strings(proxy)
    assert any(endpoint.startswith('socks5h://') for endpoint in endpoints)


def test_build_endpoint_uses_plain_socks5_when_rnds_disabled():
    proxy = _sample_proxy(rdns=False)
    endpoints = proxy_tester._build_endpoint_strings(proxy)
    assert any(endpoint.startswith('socks5://') for endpoint in endpoints)
    assert all('socks5h://' not in endpoint for endpoint in endpoints)


def test_run_proxy_probe_parses_json_payload():
    payload = {
        'ip': '62.244.1.225',
        'hostname': 'example.host',
        'provider': 'Lucky Net Ltd',
        'country': 'Ukraine',
        'city': 'Hlevakha',
    }

    def fake_request(endpoint, url, timeout):
        assert endpoint.startswith('socks5://user:pass@127.0.0.1:9050')
        assert '2ip.ua' in url
        assert timeout == 10
        return _DummyResponse(json_payload=payload)

    response = proxy_tester.run_proxy_probe(_sample_proxy(), timeout_seconds=10, request_func=fake_request)
    assert response.details['ip'] == payload['ip']
    assert response.details['location'] == 'Hlevakha, Ukraine'
    assert response.endpoint.startswith('socks5://')


def test_run_proxy_probe_falls_back_to_text_and_second_endpoint():
    text_payload = """ip : 8.8.8.8\nhostname : dns.google\nprovider : Google LLC\nlocation : United States"""
    calls = []

    def fake_request(endpoint, url, timeout):
        calls.append(endpoint)
        if len(calls) == 1:
            raise RuntimeError('first endpoint failed')
        return _DummyResponse(text=text_payload)

    proxy = _sample_proxy(socks5_port=None, http_port=8080, port=1080, type='http')
    response = proxy_tester.run_proxy_probe(proxy, request_func=fake_request)
    assert len(calls) == 2  # second attempt succeeded
    assert response.details['hostname'] == 'dns.google'
    assert response.details['provider'] == 'Google LLC'
    assert response.details['location'] == 'United States'


def test_run_proxy_probe_raises_after_all_failures():
    def failing_request(*_args, **_kwargs):
        raise RuntimeError('boom')

    proxy = _sample_proxy(active=True)
    with pytest.raises(RuntimeError) as exc:
        proxy_tester.run_proxy_probe(proxy, request_func=failing_request)
    assert "Failed to reach" in str(exc.value)


@pytest.mark.asyncio
async def test_async_wrapper_returns_serializable_dict(monkeypatch):
    payload = {
        'ip': '1.2.3.4',
        'hostname': 'host',
        'provider': 'ISP',
        'country': 'UA'
    }

    def fake_probe(proxy_data, test_url, timeout, _request):
        response = _DummyResponse(json_payload=payload)
        return proxy_tester.ProxyTestResponse(
            proxy_name=proxy_data['proxy_name'],
            endpoint='socks5://127.0.0.1:9050',
            target_url=test_url,
            latency_ms=12.34,
            status_code=200,
            details=proxy_tester._parse_json_payload(payload),
        )

    monkeypatch.setattr(proxy_tester, 'run_proxy_probe', fake_probe)

    result = await proxy_tester.test_proxy_connectivity(_sample_proxy(), timeout_seconds=5)
    assert result['details']['ip'] == '1.2.3.4'
    assert 'latency_ms' in result
