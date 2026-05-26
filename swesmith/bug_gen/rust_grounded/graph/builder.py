"""Build dependency graphs for the repository."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

import networkx as nx


@dataclass
class EdgeInfo:
    """Edge information."""
    edge_type: str  # "calls", "imports", "uses", "implements"
    weight: float = 1.0
    metadata: dict = field(default_factory=dict)


class GraphBuilder:
    """Build and analyze repository graphs."""

    def __init__(self, ast_extractor):
        self.ast_extractor = ast_extractor
        self.call_graph = nx.DiGraph()
        self.import_graph = nx.DiGraph()
        self.module_graph = nx.DiGraph()
        self.cochange_graph = nx.Graph()

    def build_all_graphs(self) -> None:
        """Build all repository graphs."""
        self._build_call_graph()
        self._build_import_graph()
        self._build_module_graph()

    def _build_call_graph(self) -> None:
        """Build function call graph."""
        # Add all functions as nodes
        for func_key, func in self.ast_extractor.functions.items():
            self.call_graph.add_node(
                func_key,
                name=func.name,
                file_path=func.file_path,
                line_start=func.line_start,
                visibility=func.visibility,
            )

        # Add edges for function calls
        for func_key, func in self.ast_extractor.functions.items():
            for callee_name in func.calls:
                # Find the callee
                for other_key, other_func in self.ast_extractor.functions.items():
                    if other_func.name == callee_name:
                        self.call_graph.add_edge(
                            func_key,
                            other_key,
                            edge_type="calls",
                        )

    def _build_import_graph(self) -> None:
        """Build import/use graph."""
        files = set(f.file_path for f in self.ast_extractor.functions.values())

        for file_path in files:
            self.import_graph.add_node(file_path)

        # Connect files that import from each other
        for imp in self.ast_extractor.imports:
            source_file = imp.file_path

            # Parse import path to find target
            # e.g., "crate::payment::service" -> look for service.rs in payment/
            path_parts = imp.path.split("::")

            if len(path_parts) >= 2:
                # Heuristic: find files that might match
                for target_file in files:
                    if path_parts[-1] in target_file:
                        self.import_graph.add_edge(
                            source_file,
                            target_file,
                            edge_type="imports",
                        )

    def _build_module_graph(self) -> None:
        """Build module hierarchy graph."""
        for mod in self.ast_extractor.modules:
            self.module_graph.add_node(
                mod.name,
                file_path=mod.file_path,
                line=mod.line,
            )

    def get_related_functions(self, func_key: str, depth: int = 2) -> List[str]:
        """Get functions related by calls (callers and callees)."""
        if func_key not in self.call_graph:
            return []

        related = set()

        # Get neighbors at specified depth
        for neighbor in nx.single_source_shortest_path_length(
            self.call_graph, func_key, cutoff=depth
        ).keys():
            if neighbor != func_key:
                related.add(neighbor)

        return list(related)

    def get_connected_files(self, file_path: str, depth: int = 2) -> List[str]:
        """Get files connected by imports or function calls."""
        connected = set()

        # Get functions in this file
        file_funcs = [
            k for k, f in self.ast_extractor.functions.items()
            if f.file_path == file_path
        ]

        # Get related functions and their files
        for func in file_funcs:
            related = self.get_related_functions(func, depth)
            for rel_func in related:
                if rel_func in self.ast_extractor.functions:
                    connected.add(self.ast_extractor.functions[rel_func].file_path)

        # Remove self
        connected.discard(file_path)

        return list(connected)

    def find_clusters(self) -> List[List[str]]:
        """Find clusters of tightly-coupled functions."""
        # Use community detection
        try:
            from networkx.algorithms import community

            undirected = self.call_graph.to_undirected()
            communities = community.greedy_modularity_communities(undirected)

            return [list(c) for c in communities[:10]]  # Top 10 clusters

        except ImportError:
            # Fallback: return connected components
            return [list(c) for c in nx.weakly_connected_components(self.call_graph)]

    def get_centrality_scores(self) -> Dict[str, float]:
        """Get centrality scores for functions."""
        if not self.call_graph.nodes():
            return {}

        try:
            centrality = nx.betweenness_centrality(self.call_graph)
            return centrality
        except Exception:
            return {}

    def get_file_dependencies(self, file_path: str) -> Dict[str, List[str]]:
        """Get all dependencies for a file."""
        funcs_in_file = [
            k for k, f in self.ast_extractor.functions.items()
            if f.file_path == file_path
        ]

        dependencies = {
            "calls": [],
            "called_by": [],
            "imports": [],
        }

        for func_key in funcs_in_file:
            func = self.ast_extractor.functions[func_key]

            # Functions this file calls
            for callee in func.calls:
                dependencies["calls"].append(callee)

            # Functions that call this
            for caller_key in func.callers:
                if caller_key in self.ast_extractor.functions:
                    caller = self.ast_extractor.functions[caller_key]
                    dependencies["called_by"].append(caller.name)

        return dependencies
