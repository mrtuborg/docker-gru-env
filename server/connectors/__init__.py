"""Auto-register all connector classes when this package is imported."""
from .github_connector     import GitHubConnector
from .copilot_connector    import CopilotConnector
from .azure_connector      import AzureConnector
from .obsidian_connector   import ObsidianConnector
from .analytics_connector  import AnalyticsConnector, IAnalyticsStore

# Register connector types
from ..connector_manager import _REGISTRY

_REGISTRY["github"]    = GitHubConnector
_REGISTRY["copilot"]   = CopilotConnector
_REGISTRY["azure"]     = AzureConnector
_REGISTRY["obsidian"]  = ObsidianConnector
_REGISTRY["analytics"] = AnalyticsConnector

__all__ = [
    "GitHubConnector", "CopilotConnector", "AzureConnector",
    "ObsidianConnector", "AnalyticsConnector", "IAnalyticsStore",
]
