from pathlib import Path
from unittest.mock import MagicMock
import httpx
import pytest
from vad.audit.centralized_hooks import CentralizedHTTPDispatcher, HTTPDispatcherConfig
from vad.audit.local_audit import AuditEvent


def test_centralized_dispatcher_batch_send(tmp_path):
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(status_code=200)

    config = HTTPDispatcherConfig(
        endpoint_url="http://127.0.0.1:8080/events",
        batch_size=2,
        offline_buffer_path=str(tmp_path / "buffer.jsonl"),
    )
    dispatcher = CentralizedHTTPDispatcher(config, client=mock_client)

    ev1 = AuditEvent(session_id="s1", agent_id="a1", action="PreTool", outcome="allow")
    ev2 = AuditEvent(session_id="s1", agent_id="a1", action="PostTool", outcome="allow")

    # Enqueue first: batch not full
    dispatcher.enqueue(ev1)
    assert mock_client.post.call_count == 0

    # Enqueue second: triggers flush
    dispatcher.enqueue(ev2)
    assert mock_client.post.call_count == 1


def test_centralized_dispatcher_offline_buffering_and_drain(tmp_path):
    buffer_file = tmp_path / "offline_buffer.jsonl"
    mock_client = MagicMock(spec=httpx.Client)

    # 1. Simulate network failure on flush
    mock_client.post.side_effect = httpx.ConnectError("Network unreachable")

    config = HTTPDispatcherConfig(
        endpoint_url="http://127.0.0.1:8080/events",
        batch_size=1,
        offline_buffer_path=str(buffer_file),
    )
    dispatcher = CentralizedHTTPDispatcher(config, client=mock_client)

    ev = AuditEvent(session_id="s-offline", agent_id="a1", action="Stop", outcome="recorded")
    dispatcher.enqueue(ev)

    # Verify event was saved to offline buffer file
    assert buffer_file.exists()
    assert "s-offline" in buffer_file.read_text()

    # 2. Simulate network restored -> drain buffer
    mock_client.post.side_effect = None
    mock_client.post.return_value = httpx.Response(status_code=200)

    drained = dispatcher.drain_offline_buffer()
    assert drained == 1
    assert buffer_file.read_text().strip() == ""
