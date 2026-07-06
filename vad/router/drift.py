from vad.router.providers.interface import ProviderInventory


def detect_provider_drift(expected: ProviderInventory, actual: ProviderInventory) -> dict:
    warnings = []
    denials = []
    expected_models = {model.name: model for model in expected.models}
    actual_models = {model.name: model for model in actual.models}

    for model_name, expected_model in expected_models.items():
        actual_model = actual_models.get(model_name)
        if actual_model is None:
            denials.append(f"model removed: {model_name}")
            continue
        if actual_model.context_limit < expected_model.context_limit:
            denials.append(f"context limit decreased for {model_name}")
        if actual_model.tier != expected_model.tier:
            warnings.append(f"tier changed for {model_name}")
        if set(actual_model.capabilities) != set(expected_model.capabilities):
            warnings.append(f"capabilities changed for {model_name}")

    return {
        "event": "provider_metadata_drift",
        "provider": actual.provider_name,
        "allow": not denials,
        "warnings": warnings,
        "denials": denials,
    }
