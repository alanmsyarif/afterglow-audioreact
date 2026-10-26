"""Connect native single-spline curves into length-weighted routes without helper objects."""
import heapq
import numpy as np
import bpy
from mathutils import Vector, kdtree


def path(obj, depsgraph):
    """Visible centerline samples, retaining native evaluated Bezier/NURBS curvature."""
    if obj.type != 'CURVE' or len(obj.data.splines) != 1:
        return None
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        if not mesh.vertices:
            return None
        xyz = np.array([tuple(v.co) for v in mesh.vertices], dtype=float)
        uv = mesh.uv_layers.get('UVMap')
        if uv and mesh.loops:
            # A bevel ring shares longitudinal U. Corner UVs retain the closed seam.
            indices = np.array([loop.vertex_index for loop in mesh.loops])
            u = np.round(np.array([p.uv.x for p in uv.data]), 6)
            keys, groups = np.unique(u, return_inverse=True)
            counts = np.bincount(groups)
            xyz = np.array([np.bincount(groups, weights=xyz[indices, k]) / counts for k in range(3)]).T
        mw = np.array(obj.matrix_world)
        xyz = xyz @ mw[:3, :3].T + mw[:3, 3]
        length = float(np.linalg.norm(np.diff(xyz, axis=0), axis=1).sum())
        if obj.data.splines[0].use_cyclic_u and np.linalg.norm(xyz[-1] - xyz[0]) > 1e-5:
            length += float(np.linalg.norm(xyz[-1] - xyz[0]))
        return xyz[0], xyz[-1], max(length, 1e-6)
    finally:
        ev.to_mesh_clear()


def layout(scene, objects, depsgraph):
    """Return object -> (start, end, loop). Branches split; closed chains wrap."""
    s = scene.afterglow
    curves = [o for o in objects if o.type == 'CURVE']
    result = {o: (0., 1., bool(o.data.splines) and all(p.use_cyclic_u for p in o.data.splines)) for o in curves}
    if not s.ripple_connect or s.ripple_style == 'CLASSIC':
        return result
    edges = []
    for obj in sorted(curves, key=lambda o: o.name):
        if len(obj.data.splines) != 1 or obj.data.splines[0].use_cyclic_u:
            continue
        info = path(obj, depsgraph)
        if info:
            edges.append((obj, *info))
    if len(edges) < 2:
        return result
    points = np.array([p for _, a, b, _ in edges for p in (a, b)])
    distance = s.ripple_join_distance or max(float(np.linalg.norm(np.ptp(points, axis=0))) * .001, 1e-5)
    tree = kdtree.KDTree(len(points))
    for i, p in enumerate(points):
        tree.insert(Vector(p), i)
    tree.balance()
    parents = list(range(len(points)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for i, p in enumerate(points):
        for _, j, _ in tree.find_range(Vector(p), distance):
            a, b = root(i), root(j)
            parents[max(a, b)] = min(a, b)
    ends = [(root(2*i), root(2*i+1)) for i in range(len(edges))]
    adjacency = {}
    for i, (a, b) in enumerate(ends):
        adjacency.setdefault(a, []).append(i)
        adjacency.setdefault(b, []).append(i)
    remaining = set(range(len(edges)))
    while remaining:
        seed = min(remaining)
        component, todo = set(), [seed]
        while todo:
            i = todo.pop()
            if i in component:
                continue
            component.add(i)
            for endpoint in ends[i]:
                todo.extend(j for j in adjacency[endpoint] if j not in component)
        remaining -= component
        if len(component) < 2:
            continue
        vertices = {v for i in component for v in ends[i]}
        tips = [v for v in vertices if len(adjacency[v]) == 1]
        # Prefer the authored start of the first curve when it is a network tip.
        start = ends[seed][0] if ends[seed][0] in tips or not tips else min(tips)
        if s.ripple_from == 'EMPTY' and s.ripple_origin:
            origin = np.array(s.ripple_origin.matrix_world.translation)
            start = min(tips or vertices, key=lambda v: float(np.linalg.norm(points[v] - origin)))
        closed = not tips and all(len(adjacency[v]) == 2 for v in vertices)
        spans = {}
        if closed:
            current, travelled, used = start, 0., set()
            while len(used) < len(component):
                i = min(j for j in adjacency[current] if j not in used)
                a, b = ends[i]
                length = edges[i][3]
                spans[i] = (travelled, travelled + length) if current == a else (travelled + length, travelled)
                travelled += length
                used.add(i)
                current = b if current == a else a
            extent = travelled
        else:
            distances, queue = {start: 0.}, [(0., start)]
            while queue:
                d, v = heapq.heappop(queue)
                if d != distances[v]:
                    continue
                for i in adjacency[v]:
                    a, b = ends[i]
                    other = b if v == a else a
                    nd = d + edges[i][3]
                    if nd < distances.get(other, float('inf')):
                        distances[other] = nd
                        heapq.heappush(queue, (nd, other))
            for i in component:
                a, b = ends[i]
                length = edges[i][3]
                spans[i] = (distances[a], distances[a] + length) if distances[a] <= distances[b] else (distances[b] + length, distances[b])
            extent = max(max(v) for v in spans.values())
        for i, (a, b) in spans.items():
            result[edges[i][0]] = (a / extent, b / extent, closed)
    return result
