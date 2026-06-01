import httpx

from app.common import http_client, tracing


def mock_handler(request):
    request_id = request.headers.get("x-cdp-request-id", "")
    return httpx.Response(200, text=request_id)


def test_trace_id_missing():
    tracing.ctx_trace_id.set("")
    client = httpx.Client(
        event_hooks={"request": [http_client.hook_request_tracing]},
        transport=httpx.MockTransport(mock_handler),
    )
    resp = client.get("http://localhost:1234/test")
    assert resp.text == ""


def test_trace_id_set():
    tracing.ctx_trace_id.set("trace-id-value")
    client = httpx.Client(
        event_hooks={"request": [http_client.hook_request_tracing]},
        transport=httpx.MockTransport(mock_handler),
    )
    resp = client.get("http://localhost:1234/test")
    assert resp.text == "trace-id-value"
