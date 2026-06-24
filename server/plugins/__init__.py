"""Auto-register all plugin classes when this package is imported."""
from .github_plugin   import GitHubPlugin
from .copilot_plugin  import CopilotPlugin
from .azure_plugin    import AzurePlugin
from .obsidian_plugin import ObsidianPlugin

# Register plugin types
from ..plugin_manager import _REGISTRY

_REGISTRY["github"]   = GitHubPlugin
_REGISTRY["copilot"]  = CopilotPlugin
_REGISTRY["azure"]    = AzurePlugin
_REGISTRY["obsidian"] = ObsidianPlugin

__all__ = ["GitHubPlugin", "CopilotPlugin", "AzurePlugin", "ObsidianPlugin"]
