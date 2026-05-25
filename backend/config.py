"""Application configuration — loaded from config.yaml at startup."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).parent.parent


@dataclass
class DeviceConfig:
    port: Optional[str] = None
    baudrate: int = 115_200
    timeout: float = 2.0
    mock: bool = False


@dataclass
class ReceiverConfig:
    default_frequency: int = 145_800_000
    default_mode: int = 1
    default_attenuator: int = 0


@dataclass
class SpectrumConfig:
    center_hz: int = 145_800_000
    span_hz: int = 500_000
    fps: float = 10.0
    db_min: float = -120.0
    db_max: float = -20.0


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class AppConfig:
    device: DeviceConfig = field(default_factory=DeviceConfig)
    receiver: ReceiverConfig = field(default_factory=ReceiverConfig)
    spectrum: SpectrumConfig = field(default_factory=SpectrumConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load configuration from YAML file; missing keys fall back to dataclass defaults."""
    cfg_path = path or ROOT / "config.yaml"
    if not cfg_path.exists():
        return AppConfig()

    with open(cfg_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    return AppConfig(
        device=DeviceConfig(**data.get("device", {})),
        receiver=ReceiverConfig(**data.get("receiver", {})),
        spectrum=SpectrumConfig(**data.get("spectrum", {})),
        server=ServerConfig(**data.get("server", {})),
    )
