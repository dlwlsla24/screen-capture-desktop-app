"""Map logical desktop rectangles to the original portal image."""

def crop_bounds(regions, width, height, start, end):
    left = min(x for x, y, w, h in regions)
    top = min(y for x, y, w, h in regions)
    right = max(x + w for x, y, w, h in regions)
    bottom = max(y + h for x, y, w, h in regions)
    x0, x1 = sorted(min(right, max(left, x)) for x in (start[0], end[0]))
    y0, y1 = sorted(min(bottom, max(top, y)) for y in (start[1], end[1]))

    # Every vertical strip must be covered, including at staggered seams.
    edges = {x0, x1}
    for x, y, w, h in regions:
        edges.update(edge for edge in (x, x + w) if x0 < edge < x1)
    edges = sorted(edges)
    for a, b in zip(edges, edges[1:]):
        covered = y0
        for lo, hi in sorted((y, y + h) for x, y, w, h in regions
                             if x <= a and b <= x + w):
            if lo > covered:
                break
            covered = max(hi, covered)
        if covered < y1:
            return None
    px, qx = (round((x - left) * width / (right - left)) for x in (x0, x1))
    py, qy = (round((y - top) * height / (bottom - top)) for y in (y0, y1))
    if px < qx and py < qy:
        return px, py, qx - px, qy - py
    return None


def match_outputs(granted, layout, outputs):
    """Resolve portal logical regions to GTK3/X11 monitor geometries.

    Some XWayland versions expose synthetic connector names and a different
    root scale. Use the complete monitor layout, even for a subset grant.
    """
    def relative(region, desktop):
        left = min(x for x, y, w, h in desktop)
        top = min(y for x, y, w, h in desktop)
        width = max(x + w for x, y, w, h in desktop) - left
        height = max(y + h for x, y, w, h in desktop) - top
        x, y, w, h = region
        return ((x - left) / width, (y - top) / height, w / width, h / height)

    if len(layout) != len(outputs):
        raise ValueError("XWayland 모니터 배치가 변경되었습니다. 다시 캡처해 주세요.")
    targets = [r for _, r in outputs]
    result = []
    for name, region in granted:
        matches = [r for n, r in outputs if name and n == name]
        if not matches:
            source = relative(region, layout)
            matches = [r for r in targets if all(abs(a - b) < 1e-6 for a, b in
                                                  zip(source, relative(r, targets)))]
        if len(matches) != 1 or matches[0] in result:
            raise ValueError("XWayland 모니터 위치를 확인할 수 없습니다. 다시 캡처해 주세요.")
        result.append(matches[0])
    return result
