from __future__ import annotations

from drydock.cli.update_notifier.adapters.filesystem_update_cache_repository import (
    FileSystemUpdateCacheRepository,
)
from drydock.cli.update_notifier.adapters.github_update_gateway import GitHubUpdateGateway
from drydock.cli.update_notifier.adapters.pypi_update_gateway import PyPIUpdateGateway
from drydock.cli.update_notifier.ports.update_cache_repository import (
    UpdateCache,
    UpdateCacheRepository,
)
from drydock.cli.update_notifier.ports.update_gateway import (
    DEFAULT_GATEWAY_MESSAGES,
    Update,
    UpdateGateway,
    UpdateGatewayCause,
    UpdateGatewayError,
)
from drydock.cli.update_notifier.update import (
    UpdateAvailability,
    UpdateError,
    get_update_if_available,
)
from drydock.cli.update_notifier.whats_new import (
    load_whats_new_content,
    mark_version_as_seen,
    should_show_whats_new,
)

__all__ = [
    "DEFAULT_GATEWAY_MESSAGES",
    "FileSystemUpdateCacheRepository",
    "GitHubUpdateGateway",
    "PyPIUpdateGateway",
    "Update",
    "UpdateAvailability",
    "UpdateCache",
    "UpdateCacheRepository",
    "UpdateError",
    "UpdateGateway",
    "UpdateGatewayCause",
    "UpdateGatewayError",
    "get_update_if_available",
    "load_whats_new_content",
    "mark_version_as_seen",
    "should_show_whats_new",
]
