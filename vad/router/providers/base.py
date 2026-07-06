from abc import ABC, abstractmethod
from typing import Optional
from vad.router.models import ModelInfo

class BaseProvider(ABC):
    @abstractmethod
    def get_model(self, name: str) -> Optional[ModelInfo]:
        pass

    def list_models(self) -> list[ModelInfo]:
        return []

class DummyProvider(BaseProvider):
    def __init__(self):
        self.models = {
            "expensive-model": ModelInfo(name="expensive-model", tier="tier2", cost_per_1k_tokens=0.1, max_tokens=8192),
            "cheap-model": ModelInfo(name="cheap-model", tier="tier1", cost_per_1k_tokens=0.01, max_tokens=4096),
        }

    def get_model(self, name: str) -> Optional[ModelInfo]:
        return self.models.get(name)

    def list_models(self) -> list[ModelInfo]:
        return list(self.models.values())
