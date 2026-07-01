"""
AI Review Agent Registry.

Provides agent discovery and management through a registry pattern.
Agents register themselves using the @register decorator.
"""

import importlib
import logging
import pkgutil

from connect_labs.labs.ai_review_agents.base import BaseAIReviewAgent

logger = logging.getLogger(__name__)


class AIReviewAgentRegistry:
    """
    Registry for AI review agents.

    Manages registration and retrieval of agent classes.

    Usage:
        # Register an agent
        @registry.register
        class MyAgent(BaseAIReviewAgent):
            agent_id = "my_agent"
            ...

        # Get an agent instance
        agent = registry.get_agent("my_agent")

        # List all agents
        for agent_id, agent_cls in registry.list_agents():
            print(agent_id, agent_cls.name)
    """

    _instance = None
    _agents: dict[str, type[BaseAIReviewAgent]]

    def __new__(cls):
        """Singleton pattern - only one registry instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._agents = {}
            cls._instance._discovered = False
        return cls._instance

    def register(self, agent_class: type[BaseAIReviewAgent]) -> type[BaseAIReviewAgent]:
        """
        Register an agent class.

        Can be used as a decorator:
            @registry.register
            class MyAgent(BaseAIReviewAgent):
                ...

        Args:
            agent_class: The agent class to register

        Returns:
            The agent class (unchanged, for decorator use)

        Raises:
            ValueError: If agent_id is not set or already registered
        """
        if not issubclass(agent_class, BaseAIReviewAgent):
            raise TypeError(f"{agent_class} must be a subclass of BaseAIReviewAgent")

        agent_id = agent_class.agent_id
        if not agent_id:
            raise ValueError(f"{agent_class.__name__} must define 'agent_id'")

        if agent_id in self._agents:
            existing = self._agents[agent_id]
            if existing is not agent_class:
                raise ValueError(
                    f"Agent ID '{agent_id}' already registered by {existing.__name__}. "
                    f"Cannot register {agent_class.__name__} with same ID."
                )
            # Same class registered twice is okay (e.g., during reload)
            return agent_class

        self._agents[agent_id] = agent_class
        logger.debug(f"Registered AI review agent: {agent_id} ({agent_class.__name__})")
        return agent_class

    def get_agent(self, agent_id: str) -> BaseAIReviewAgent:
        """
        Get an agent instance by ID.

        Creates a new instance of the agent class.

        Args:
            agent_id: The agent's unique identifier

        Returns:
            Instance of the agent

        Raises:
            KeyError: If agent_id is not registered
        """
        self._ensure_discovered()

        if agent_id not in self._agents:
            available = ", ".join(self._agents.keys()) or "(none)"
            raise KeyError(f"Unknown agent ID: '{agent_id}'. Available agents: {available}")

        return self._agents[agent_id]()

    def get_agent_class(self, agent_id: str) -> type[BaseAIReviewAgent]:
        """
        Get an agent class by ID (without instantiating).

        Args:
            agent_id: The agent's unique identifier

        Returns:
            The agent class

        Raises:
            KeyError: If agent_id is not registered
        """
        self._ensure_discovered()

        if agent_id not in self._agents:
            available = ", ".join(self._agents.keys()) or "(none)"
            raise KeyError(f"Unknown agent ID: '{agent_id}'. Available agents: {available}")

        return self._agents[agent_id]

    def list_agents(self) -> list[tuple[str, type[BaseAIReviewAgent]]]:
        """
        List all registered agents.

        Returns:
            List of (agent_id, agent_class) tuples
        """
        self._ensure_discovered()
        return list(self._agents.items())

    def is_registered(self, agent_id: str) -> bool:
        """Check if an agent ID is registered."""
        self._ensure_discovered()
        return agent_id in self._agents

    def _ensure_discovered(self):
        """Ensure agents have been discovered from the agents subpackage."""
        if not self._discovered:
            self._discover_agents()
            self._discovered = True

    def _discover_agents(self):
        """
        Auto-discover agents from the agents subpackage.

        Imports all modules in connect_labs.labs.ai_review_agents.agents
        which triggers their @register decorators.
        """
        try:
            from connect_labs.labs.ai_review_agents import agents as agents_pkg

            package_path = agents_pkg.__path__
            package_name = agents_pkg.__name__

            for _, module_name, _ in pkgutil.iter_modules(package_path):
                full_name = f"{package_name}.{module_name}"
                try:
                    importlib.import_module(full_name)
                    logger.debug(f"Discovered agent module: {full_name}")
                except Exception as e:
                    logger.warning(f"Failed to import agent module {full_name}: {e}")

        except ImportError as e:
            logger.debug(f"Could not import agents package for discovery: {e}")

    def clear(self):
        """Clear all registered agents. Mainly for testing."""
        self._agents.clear()
        self._discovered = False


# Global registry instance
registry = AIReviewAgentRegistry()


def register(agent_class: type[BaseAIReviewAgent]) -> type[BaseAIReviewAgent]:
    """
    Decorator to register an AI review agent.

    Usage:
        from connect_labs.labs.ai_review_agents import register

        @register
        class MyAgent(BaseAIReviewAgent):
            agent_id = "my_agent"
            ...
    """
    return registry.register(agent_class)


def get_agent(agent_id: str) -> BaseAIReviewAgent:
    """
    Get an AI review agent instance by ID.

    Convenience function that uses the global registry.

    Args:
        agent_id: The agent's unique identifier

    Returns:
        Instance of the agent
    """
    return registry.get_agent(agent_id)


def list_agents() -> list[tuple[str, type[BaseAIReviewAgent]]]:
    """
    List all registered AI review agents.

    Convenience function that uses the global registry.

    Returns:
        List of (agent_id, agent_class) tuples
    """
    return registry.list_agents()
