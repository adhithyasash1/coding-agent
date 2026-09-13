"""Optional Pier 0.3.1 contract over the existing Harbor execution transport."""

from pier.agents.base import BaseAgent as PierBaseAgent
from pier.models.agent.install import AgentInstallSpec
from pier.models.agent.network import NetworkAllowlist
from pier.models.trial.result import AgentInfo, ModelInfo

from integrations.harbor_agent import AdaptiveAgent as HarborAdaptiveAgent


class AdaptiveAgent(HarborAdaptiveAgent, PierBaseAgent):
    """Reuse Harbor setup/run, with Pier metadata and environment creation hooks.

    ``commit_patch`` is inherited and defaults to False. Enable it explicitly
    for tasks whose collection script reads committed changes from the checkout.
    """

    SUPPORTS_WINDOWS = False
    BENCHMARK_BACKEND = "pier"

    def to_agent_info(self) -> AgentInfo:
        # Use the effective config even when Pier's optional model_name is absent.
        # Pier's Pydantic classes do not accept Harbor model instances.
        model_name = self.model_name or self.config.model.name
        provider, separator, name = model_name.partition("/")
        return AgentInfo(
            name=self.name(),
            version=self.version(),
            model_info=ModelInfo(
                name=name if separator else model_name,
                provider=provider if separator else None,
            ),
        )

    def install_spec(self) -> AgentInstallSpec | None:
        # Setup uploads the existing tool server after environment startup.
        return None

    def network_allowlist(self) -> NetworkAllowlist:
        # Model requests happen in the runner, outside the task environment.
        return NetworkAllowlist()
