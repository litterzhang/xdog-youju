"""Official application capability discovery with the existing sandbox transport.

Never publish the parent's raw CLI executor: official Flow commands execute only
inside the same namespace as Bash/filesystem. This is not a Flow implementation.
"""

from xdog.agent.capabilities import ApplicationPort, CapabilityAssembly, CapabilitySet

FLOW_CAPABILITY_PROFILE = "flow-application-sandbox-v1"


class SandboxedFlowCapabilities:
    def __init__(self):
        self.port = ApplicationPort()
        self.capabilities = CapabilitySet("application")
        self.assembly = CapabilityAssembly(self.capabilities)
        self.closed = False

    async def start(self):
        # Do not silently activate newly installed application integrations.
        if {provider.name for provider in self.capabilities.providers} != {"flow"}:
            raise ValueError("Unexpected installed author application capabilities")
        await self.capabilities.start(self.port)
        if self.assembly.declared_tools != {"flow"} or "flow" not in self.assembly.declared_skills:
            raise ValueError("Official Flow capability is unavailable")

    def create_agent(self, stream_fn, *, workspace, tools=(), **kwargs):
        official = self.assembly.compose_tools(enabled_tools=["flow"])
        isolated = workspace.isolate_tools(official)
        # The official inventory supplies tool schemas and ownership lifecycle.
        # Only transport is replaced; no host-side CLI executor reaches the Agent.
        return self.assembly.create_agent(
            stream_fn,
            tools=[*tools, *isolated],
            enabled_tools=[],
            **kwargs,
        )

    def restore_agent(self, agent, snapshot, *, allowed_models):
        self.assembly.restore_agent(agent, snapshot, allowed_models=allowed_models)

    async def aclose(self):
        try:
            await self.capabilities.aclose()
        finally:
            self.closed = True
