#include <iostream>
#include <queue>
#include <string>
#include <vector>

int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    int rows, columns;
    std::cin >> rows >> columns;
    std::string grid;
    for (int row = 0; row < rows; ++row) {
        std::string line;
        std::cin >> line;
        grid += line;
    }
    const int start = grid.find('S');
    const int end = grid.find('E');
    std::vector<int> distance(grid.size(), -1);
    std::queue<int> frontier;
    distance[start] = 0;
    frontier.push(start);
    while (!frontier.empty() && distance[end] == -1) {
        const int cell = frontier.front();
        frontier.pop();
        const int row = cell / columns, column = cell % columns;
        const int neighbours[4] = {cell - columns, cell + columns, cell - 1, cell + 1};
        const bool inside[4] = {row > 0, row < rows - 1, column > 0, column < columns - 1};
        for (int i = 0; i < 4; ++i) {
            const int next = neighbours[i];
            if (inside[i] && grid[next] != '#' && distance[next] == -1) {
                distance[next] = distance[cell] + 1;
                frontier.push(next);
            }
        }
    }
    std::cout << distance[end] << "\n";
}
