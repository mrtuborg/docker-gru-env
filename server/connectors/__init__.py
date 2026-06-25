"""Auto-register all connector classes when this package is imported."""
from .github_connector   import GitHubPlugin
from .copilot_connector  import CopilotPlugin
from .azure_connector    import AzurePlugin
from .obsidian_connector import ObsidianPlugin

# Register connector types
from ..connector_manager import _REGISTRY

_REGISTRY["github"]   = GitHubPlugin
_REGISTRY["copilot"]  = CopilotPlugin
_REGISTRY["azure"]    = AzurePlugin
_REGISTRY["obsidian"] = ObsidianPlugin

__all__ = ["GitHubPlugin", "CopilotPlugin", "AzurePlugin", "ObsidianPlugin"]
