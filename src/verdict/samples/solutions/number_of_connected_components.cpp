#include <iostream>
#include <numeric>
#include <vector>

std::vector<int> parent;

int root(int node) {
    while (parent[node] != node) {
        parent[node] = parent[parent[node]];
        node = parent[node];
    }
    return node;
}

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int nodes, edges;
    std::cin >> nodes >> edges;
    parent.resize(nodes + 1);
    std::iota(parent.begin(), parent.end(), 0);
    int components = nodes;
    for (int i = 0; i < edges; ++i) {
        int u, v;
        std::cin >> u >> v;
        const int root_u = root(u), root_v = root(v);
        if (root_u != root_v) {
            parent[root_u] = root_v;
            --components;
        }
    }
    std::cout << components << "\n";
}
