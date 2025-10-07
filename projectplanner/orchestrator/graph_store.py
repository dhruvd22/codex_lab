"""In-memory graph store for orchestrator component coverage."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

from projectplanner.logging_utils import get_logger
from projectplanner.orchestrator.models import GraphCoverageSnapshot, GraphNode, Milestone

LOGGER = get_logger(__name__)


class GraphStore:
    """Tracks blueprint components and their milestone coverage."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._nodes: Dict[str, GraphNode] = {}

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return normalized or "node"

    def load_components(self, components: Iterable[str]) -> None:
        """Seed the graph with top-level components from the blueprint."""

        for raw in components:
            name = (raw or "").strip()
            if not name:
                continue
            slug = self._slugify(name)
            if slug in self._nodes:
                continue
            self._nodes[slug] = GraphNode(id=slug, name=name)
            LOGGER.debug(
                "Graph node registered",
                extra={
                    "event": "orchestrator.graph.node_registered",
                    "run_id": self.run_id,
                    "payload": {"id": slug, "name": name},
                },
            )

    def upsert_node(self, name: str, *, description: str | None = None) -> GraphNode:
        slug = self._slugify(name)
        node = self._nodes.get(slug)
        if node:
            if description and not node.description:
                node.description = description
            return node
        node = GraphNode(id=slug, name=name.strip(), description=description)
        self._nodes[slug] = node
        LOGGER.debug(
            "Graph node upserted",
            extra={
                "event": "orchestrator.graph.node_upserted",
                "run_id": self.run_id,
                "payload": {"id": slug, "name": node.name},
            },
        )
        return node

    def assign_milestones(self, milestones: Iterable[Milestone]) -> None:
        """Associate nodes with any milestone mentioning them by name."""

        for milestone in milestones:
            text = f"{milestone.details} {milestone.context}".lower()
            for node in self._nodes.values():
                if node.name.lower() in text:
                    if milestone.milestone_id not in node.milestone_ids:
                        node.milestone_ids.append(milestone.milestone_id)
                        LOGGER.debug(
                            "Graph node linked to milestone",
                            extra={
                                "event": "orchestrator.graph.node_linked",
                                "run_id": self.run_id,
                                "payload": {
                                    "node": node.id,
                                    "milestone_id": milestone.milestone_id,
                                },
                            },
                        )

    def set_assignment(self, node_id: str, milestone_id: int) -> None:
        node = self._nodes.get(node_id)
        if not node:
            LOGGER.warning(
                "Attempted to assign missing node",
                extra={
                    "event": "orchestrator.graph.missing_node",
                    "run_id": self.run_id,
                    "payload": {"node": node_id, "milestone_id": milestone_id},
                },
            )
            return
        if milestone_id not in node.milestone_ids:
            node.milestone_ids.append(milestone_id)
            LOGGER.debug(
                "Manual assignment applied",
                extra={
                    "event": "orchestrator.graph.manual_assignment",
                    "run_id": self.run_id,
                    "payload": {"node": node.id, "milestone_id": milestone_id},
                },
            )

    def nodes(self) -> List[GraphNode]:
        return list(self._nodes.values())

    def coverage(self) -> Tuple[List[str], List[str]]:
        covered: List[str] = []
        uncovered: List[str] = []
        for node in self._nodes.values():
            if node.milestone_ids:
                covered.append(node.name)
            else:
                uncovered.append(node.name)
        return covered, uncovered

    def snapshot(self, notes: str | None = None) -> GraphCoverageSnapshot:
        covered, uncovered = self.coverage()
        return GraphCoverageSnapshot(
            run_id=self.run_id,
            covered_nodes=sorted(covered),
            uncovered_nodes=sorted(uncovered),
            notes=notes,
        )


__all__ = ["GraphStore"]
