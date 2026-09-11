from unittest.mock import MagicMock
import httpx
import pytest
from vad.audit.local_audit import AuditEvent
from vad.audit.siem_export import (
    DatadogLogsAdapter,
    ElasticBulkAdapter,
    SentinelDCEAdapter,
    SIEMExportFactory,
    SIEMMappingConfig,
    SIEMProvider,
    SplunkHECAdapter,
)


@pytest.fixture
def sample_event():
    return AuditEvent(
        session_id="siem-sess",
        agent_id="test-agent",
        action="QualityGate",
        tool=None,
        outcome="pass",
        metadata={"coverage": 92.5},
    )


def test_splunk_hec_formatting(sample_event):
    config = SIEMMappingConfig(
        provider=SIEMProvider.SPLUNK,
        endpoint_url="http://127.0.0.1:8088/services/collector",
        token="splunk-hec-token",
        index_name="vad_sec",
    )
    adapter = SIEMExportFactory.create(config)
    assert isinstance(adapter, SplunkHECAdapter)

    formatted = adapter.format_event(sample_event)
    assert formatted["index"] == "vad_sec"
    assert formatted["sourcetype"] == "vad:audit"
    assert formatted["event"]["session_id"] == "siem-sess"


def test_datadog_logs_formatting(sample_event):
    config = SIEMMappingConfig(
        provider=SIEMProvider.DATADOG,
        endpoint_url="http://127.0.0.1:8089/api/v2/logs",
        token="dd-api-key",
    )
    adapter = SIEMExportFactory.create(config)
    assert isinstance(adapter, DatadogLogsAdapter)

    formatted = adapter.format_event(sample_event)
    assert formatted["ddsource"] == "vad"
    assert "test-agent" in formatted["message"]


def test_siem_batch_export_mock_call(sample_event):
    config = SIEMMappingConfig(
        provider=SIEMProvider.SENTINEL,
        endpoint_url="http://127.0.0.1:8090/dataCollectionRules/dcr-123",
        token="bearer-token",
    )
    adapter = SIEMExportFactory.create(config)

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = httpx.Response(status_code=202)

    success = adapter.export_batch([sample_event], client=mock_client)
    assert success is True
    assert mock_client.post.call_count == 1
