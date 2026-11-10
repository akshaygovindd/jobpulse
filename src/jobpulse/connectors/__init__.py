"""Source connectors. Each implements jobpulse.connectors.base.Connector."""

from jobpulse.config import IngestConfig
from jobpulse.connectors.ashby import AshbyConnector
from jobpulse.connectors.base import Connector, Page
from jobpulse.connectors.greenhouse import GreenhouseConnector
from jobpulse.connectors.lever import LeverConnector
from jobpulse.http import HttpClient

__all__ = [
    "AshbyConnector",
    "Connector",
    "GreenhouseConnector",
    "LeverConnector",
    "Page",
    "build_connectors",
]


def build_connectors(http: HttpClient, cfg: IngestConfig) -> dict[str, Connector]:
    return {
        "ashby": AshbyConnector(
            http,
            include_compensation=cfg.ashby_include_compensation,
            allow_empty=cfg.ashby_allow_empty,
        ),
        "greenhouse": GreenhouseConnector(http, include_content=cfg.greenhouse_include_content),
        "lever": LeverConnector(http, page_size=cfg.lever_page_size, max_pages=cfg.max_pages),
    }
