import sys


def main() -> None:
    tokens = sys.stdin.buffer.read().split()
    nodes, edges = int(tokens[0]), int(tokens[1])
    parent = list(range(nodes + 1))

    def root(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    components = nodes
    for index in range(2, 2 + 2 * edges, 2):
        root_u, root_v = root(int(tokens[index])), root(int(tokens[index + 1]))
        if root_u != root_v:
            parent[root_u] = root_v
            components -= 1
    print(components)


main()
