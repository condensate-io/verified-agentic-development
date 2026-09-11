import json
import pytest
from pathlib import Path
from vad.deploy.managed_settings import ManagedSettingsGenerator, EnterprisePolicyConfig


def test_managed_settings_json():
    cfg = EnterprisePolicyConfig(organization="Acme Corp", max_budget_per_session_usd=10.0)
    gen = ManagedSettingsGenerator(cfg)
    bundle_str = gen.generate_json_bundle()
    bundle = json.loads(bundle_str)

    assert bundle["organization"] == "Acme Corp"
    assert bundle["governance"]["max_budget_per_session_usd"] == 10.0
    assert bundle["governance"]["enforce_vad_hooks"] is True


def test_managed_settings_plist():
    cfg = EnterprisePolicyConfig(organization="Acme Corp", bundle_id="com.acme.vad")
    gen = ManagedSettingsGenerator(cfg)
    plist_str = gen.generate_apple_mdm_plist()

    assert "<plist" in plist_str
    assert "com.acme.vad" in plist_str
    assert "<key>EnforceVADHooks</key>" in plist_str
    assert "<true/>" in plist_str


def test_managed_settings_admx():
    cfg = EnterprisePolicyConfig(bundle_id="com.acme.vad")
    gen = ManagedSettingsGenerator(cfg)
    admx_str = gen.generate_windows_admx()

    assert "<policyDefinitions" in admx_str
    assert "com.acme.vad" in admx_str
    assert "EnforceVADHooks" in admx_str


def test_managed_settings_export_all(tmp_path):
    gen = ManagedSettingsGenerator()
    out = gen.export_all(tmp_path)

    assert out["json"].exists()
    assert out["plist"].exists()
    assert out["admx"].exists()
