# -*- coding: utf-8 -*-
"""Mindmap screenshot parsing: RapidOCR, directed colored-line tracing,
and rooted tree construction. Adapted from the
provided script. Intended for left-rooted colored underline diagrams.
Classification is heuristic; results require review. Chunking is implemented
separately in mindmap_chunker.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np


# ---------------- OCR ----------------

_OCR_ENGINE = None          # 模块级缓存: 多次识别复用, 免去每次 ~2s 初始化


def _ocr_region(img_bgr, offset_x=0, offset_y=0, image_shape=None):
    """OCR an unscaled crop; RapidOCR returns coordinates in the input crop."""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr import RapidOCR
        _OCR_ENGINE = RapidOCR()
    result = _OCR_ENGINE(img_bgr)
    boxes = []
    scores = getattr(result, "scores", None)
    for index, (pts, text) in enumerate(zip(result.boxes if result.boxes is not None else [], result.txts or [])):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        if x1 - x0 < 5 or y1 - y0 < 5:
            continue
        if not text.strip():
            continue
        h, w = img_bgr.shape[:2]
        full_h, full_w = image_shape or (h, w)
        clipped = ((offset_x > 0 and x0 <= 8)
                   or (offset_y > 0 and y0 <= 8)
                   or (offset_x + w < full_w and x1 >= w - 8)
                   or (offset_y + h < full_h and y1 >= h - 8))
        x0, x1 = x0 + offset_x, x1 + offset_x
        y0, y1 = y0 + offset_y, y1 + offset_y
        boxes.append({"text": text.strip(), "boundary_clipped": clipped,
                      "ocr_confidence": float(scores[index]) if scores is not None else 0.0,
                      "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                      "cx": (x0 + x1) / 2.0, "cy": (y0 + y1) / 2.0})
    return _dedup(boxes)


def _tile_starts(length, size, overlap):
    if length <= size:
        return [0]
    starts = list(range(0, length - size + 1, size - overlap))
    if starts[-1] != length - size:
        starts.append(length - size)
    return starts


def ocr_blocks(img_bgr, tile_size=1536, overlap=256):
    """Recognize overlapping crops at native resolution, then merge globally.

    Crops stay below RapidOCR's default 2000-pixel preprocessing limit.
    Artificial-edge detections are retried on a larger, centered source crop.
    Remaining incomplete labels are retained and explicitly flagged for review.
    """
    if not 0 <= overlap < tile_size <= 2000:
        raise ValueError("Require 0 <= overlap < tile_size <= 2000")
    h, w = img_bgr.shape[:2]
    if max(h, w) <= 2000:
        return _ocr_region(img_bgr)
    # Preserve full-width text on typical long screenshots.
    tw = w if w <= 2000 else tile_size
    th = h if h <= 2000 else tile_size
    boxes = []
    for y in _tile_starts(h, th, overlap):
        for x in _tile_starts(w, tw, overlap):
            boxes.extend(_ocr_region(img_bgr[y:y+th, x:x+tw], x, y, (h, w)))
    boxes = _dedup(boxes)
    repairs = []
    regions = set()
    for b in boxes:
        if not b["boundary_clipped"]:
            continue
        # Expand twice if a multiline/long label still touches the retry edge.
        for padding in (128, 384):
            rw = min(w, 2000, b["x1"] - b["x0"] + 2 * padding)
            rh = min(h, 2000, b["y1"] - b["y0"] + 2 * padding)
            x = max(0, min(w-rw, int(b["cx"] - rw/2)))
            y = max(0, min(h-rh, int(b["cy"] - rh/2)))
            region = (x, y, rw, rh)
            if region in regions:
                continue
            regions.add(region)
            found = _ocr_region(img_bgr[y:y+rh, x:x+rw], x, y, (h, w))
            repairs.extend(found)
            if any(not candidate["boundary_clipped"] and _boxes_overlap(b, candidate)
                   for candidate in found):
                break
    return _dedup(boxes + repairs)


def _boxes_overlap(a, b):
    ox = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
    oy = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    return (ox > .6 * min(a["x1"]-a["x0"], b["x1"]-b["x0"])
            and oy > .6 * min(a["y1"]-a["y0"], b["y1"]-b["y0"]))


def filter_fold_controls(image, boxes):
    """Exclude compact OCR zeros only when their ink is predominantly colored."""
    kept, removed = [], []
    for box in boxes:
        roi = image[max(0,box["y0"]):box["y1"], max(0,box["x0"]):box["x1"]]
        width, height = box["x1"]-box["x0"], box["y1"]-box["y0"]
        control = False
        if roi.size and box["text"].strip() in {"0", "O", "o", "Θ", "θ", "⊖", "è", "E"} and .6 < width/max(height,1) < 1.5:
            colors = roi.astype(np.int16)
            spread = colors.max(axis=2)-colors.min(axis=2)
            colored = int((spread > 35).sum())
            dark = int(((spread < 25) & (colors.mean(axis=2)<150)).sum())
            control = colored >= 8 and colored > 3*dark
        (removed if control else kept).append(box)
    return kept, removed


def _dedup(boxes):
    boxes = sorted(boxes, key=lambda b: (b.get("boundary_clipped", False),
                   -(b["x1"] - b["x0"]) * (b["y1"] - b["y0"]),
                   -b.get("ocr_confidence", 0)))
    kept = []
    for b in boxes:
        dup = False
        for k in kept:
            ox = min(b["x1"], k["x1"]) - max(b["x0"], k["x0"])
            oy = min(b["y1"], k["y1"]) - max(b["y0"], k["y0"])
            if ox > 0.6 * min(b["x1"] - b["x0"], k["x1"] - k["x0"]) and \
               oy > 0.6 * min(b["y1"] - b["y0"], k["y1"] - k["y0"]):
                dup = True
                break
        if not dup:
            kept.append(b)
    return kept


# ---------------- 墨迹分离 ----------------

def split_ink(img_bgr):
    """分离 背景 / 深色文字 / 连线网络。
    背景色 = 图像众数（粗量化）；net = 非背景 且 非深色文字，
    涵盖连接线、锚点圆、节点底色及它们之间的抗锯齿过渡带。
    返回 (net, dark_ink)，net 已做 3x3 膨胀桥接。"""
    shape = img_bgr.shape[:2]
    data = img_bgr.reshape(-1, 3).astype(np.int16)
    q = (data // 16).astype(np.int32)
    keys = q[:, 0] * 65536 + q[:, 1] * 256 + q[:, 2]
    uniq, counts = np.unique(keys, return_counts=True)
    bgq = int(uniq[np.argmax(counts)])
    bg = np.array([(bgq >> 16) * 16 + 8,
                   ((bgq >> 8) & 255) * 16 + 8,
                   (bgq & 255) * 16 + 8], np.int16)
    diff = np.abs(data - bg).max(axis=1).reshape(shape)
    non_bg = diff > 14

    b, g, r = cv2.split(img_bgr.astype(np.int16))
    mx = np.maximum(np.maximum(b, g), r)
    mn = np.minimum(np.minimum(b, g), r)
    spread = (mx - mn).astype(np.uint8)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    dark_ink = ((spread <= 25) & (gray < 150)).astype(np.uint8) * 255

    net = (non_bg & ~(cv2.dilate(dark_ink, np.ones((3, 3), np.uint8)) > 0)) \
        .astype(np.uint8) * 255
    net = cv2.dilate(net, np.ones((3, 3), np.uint8))
    return net, dark_ink


def _same_row(a, b):
    ov = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    return ov > 0.5 * min(a["y1"] - a["y0"], b["y1"] - b["y0"])


def _h_gap(a, b):
    return max(b["x0"] - a["x1"], a["x0"] - b["x1"])


def premerge_row_fragments(boxes, gap=25):
    """同行紧贴(gap<25px)的 OCR 碎片预合并为一个标签。
    真正的同行父子节点间距 ≥ 40px(中间有折叠按钮), 不会被误合。"""
    boxes = [dict(b) for b in boxes]
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if _same_row(a, b) and _h_gap(a, b) < gap:
                    if a["x0"] <= b["x0"]:
                        li, ri = a, b
                    else:
                        li, ri = b, a
                    li["text"] = li["text"] + ri["text"]
                    li["x0"] = min(li["x0"], ri["x0"])
                    li["y0"] = min(li["y0"], ri["y0"])
                    li["x1"] = max(li["x1"], ri["x1"])
                    li["y1"] = max(li["y1"], ri["y1"])
                    li["cx"] = (li["x0"] + li["x1"]) / 2
                    li["cy"] = (li["y0"] + li["y1"]) / 2
                    boxes.pop(j if ri is b else i)
                    changed = True
                    break
            if changed:
                break
    return boxes


# ---------------- 连线检测: 沿真实线条向左追踪 ----------------

def detect_edges(img_bgr, boxes, **kwargs):
    """Trace each underline inlet leftwards to a parent outlet on real ink.

    Monotone leftwards paths cannot travel out along a sibling's rightwards
    branch. The first eligible parent outlet terminates the search.
    """
    import heapq
    net, _ = split_ink(img_bgr)
    mask = net > 0
    h, w = mask.shape
    # Closed colored rectangles are node containers, not underlines. Use their
    # side midpoints as ports so a long incoming curve need not cross the box.
    colors = img_bgr.astype(np.int16)
    blue = ((colors[:, :, 0] - colors[:, :, 2] > 35)
            & (colors[:, :, 0] - colors[:, :, 1] > 10)).astype(np.uint8)*255
    contours, _ = cv2.findContours(blue, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    rectangles = []
    for contour in contours:
        x, y, rw, rh = cv2.boundingRect(contour)
        if rw < 30 or rh < 12 or rh > 150 or rw > 1500:
            continue
        if cv2.contourArea(contour) < .75 * rw * rh:
            continue
        rectangles.append((x, y, x+rw-1, y+rh-1))
    anchors = []
    for box in boxes:
        height = box["y1"] - box["y0"]
        candidates = [r for r in rectangles
                      if r[0] <= box["x0"] and r[1] <= box["y0"]
                      and r[2] >= box["x1"] and r[3] >= box["y1"]
                      and r[2]-r[0] < box["x1"]-box["x0"]+4*height
                      and r[3]-r[1] < 3*height]
        if candidates:
            rect = min(candidates, key=lambda r: (r[2]-r[0])*(r[3]-r[1]))
            y = int((rect[1]+rect[3])/2)
            anchors.append((rect[0], rect[2], y))
            box["node_rect"] = list(rect)
            box["line_anchor"] = [rect[0], rect[2], y]
            continue
        xa, xb = max(0, box["x0"]), min(w, box["x1"])
        ya, yb = max(0, box["y1"] - 2), min(h, box["y1"] + max(8, int(height * .65)))
        if xa >= xb or ya >= yb:
            anchors.append(None)
            continue
        rows = mask[ya:yb, xa:xb].sum(axis=1)
        y = ya + int(np.argmax(rows))
        if rows.max() < max(5, (xb-xa)*.35):
            anchors.append(None)
            continue
        xs = np.flatnonzero(mask[y, xa:xb]) + xa
        # Restrict endpoints to the associated label, not the whole line network.
        left, right = int(xs[0]), int(xs[-1])
        anchors.append((left, right, y))
        box["line_anchor"] = [left, right, y]

    edges = []
    root = min(range(len(boxes)), key=lambda i: boxes[i]["x0"]) if boxes else None
    for child, anchor in enumerate(anchors):
        if anchor is None or child == root:
            continue
        left, _, sy = anchor
        targets = {}
        for parent, other in enumerate(anchors):
            if parent == child or other is None:
                continue
            pl, pr, py = other
            if pl >= left - 8 or pr > left + 5:
                continue
            for yy in range(max(0, py-2), min(h, py+3)):
                for xx in range(max(pl, pr-5), min(w, pr+4)):
                    if mask[yy, xx]:
                        targets[(xx, yy)] = parent
        # Root text may sit inside a filled rectangle: its right border is outlet.
        if root is not None and root != child:
            r = boxes[root]
            if r.get("node_rect"):
                rx0, ry0, rx1, ry1 = r["node_rect"]
                # Long maps fan vertically out of the top/bottom of the root.
                # Include the entire perimeter rather than just its right side.
                for yy in range(max(0, ry0-3), min(h, ry1+4)):
                    for xx in range(max(0, rx0-3), min(w, rx1+4, left-1)):
                        if (xx <= rx0+3 or xx >= rx1-3 or yy <= ry0+3 or yy >= ry1-3) and mask[yy, xx]:
                            targets.setdefault((xx, yy), root)
            if r["x1"] < left:
                for yy in range(max(0,r["y0"]-8), min(h,r["y1"]+12)):
                    for xx in range(max(0,r["x1"]-3), min(w,r["x1"]+35)):
                        if mask[yy,xx]:
                            targets.setdefault((xx,yy),root)
        heap = [(0, left, sy)]
        distances = {(left, sy): 0}
        found = None
        while heap:
            cost, x, y = heapq.heappop(heap)
            if cost > distances[(x,y)]:
                continue
            if (x,y) in targets:
                found = targets[(x,y)]
                break
            for dx,dy in ((-1,0),(-1,-1),(-1,1),(0,-1),(0,1)):
                nx,ny=x+dx,y+dy
                if not (0<=nx<w and 0<=ny<h) or not mask[ny,nx]:
                    continue
                next_cost = cost + (1.414 if dx and dy else 1)
                if next_cost < distances.get((nx,ny), float('inf')):
                    distances[(nx,ny)] = next_cost
                    heapq.heappush(heap,(next_cost,nx,ny))
        if found is not None:
            edges.append((found,child,0))
    return edges


# ---------------- 行内碎片合并 ----------------

def merge_fragments(boxes, edges, gap=130):
    """无边（无连接线触碰）的文字块 → 并入同行近邻（续文）。
    返回 (新 boxes, 重映射后的 edges)。"""
    deg = set()
    for a, b, _ in edges:
        deg.add(a)
        deg.add(b)
    alive = [dict(b) for b in boxes]          # 每块的存活副本
    owner = list(range(len(boxes)))           # 原下标 -> 存活下标
    edge_set = {(a, b) for a, b, _ in edges}

    changed = True
    while changed:
        changed = False
        orphans = [i for i in range(len(alive))
                   if alive[i] and i not in
                   {owner[j] for j in deg if owner[j] is not None}]
        # deg 内的存活块
        linked = {owner[j] for j in deg if owner[j] is not None and alive[owner[j]]}
        for i in range(len(alive)):
            if not alive[i] or i in linked:
                continue
            # 孤块找同行近邻（优先左邻）
            cands = []
            for j in range(len(alive)):
                if j == i or not alive[j]:
                    continue
                if _same_row(alive[i], alive[j]) and _h_gap(alive[i], alive[j]) < gap:
                    cands.append((_h_gap(alive[i], alive[j]),
                                  -alive[j]["x0"], j))
            if not cands:
                continue
            _, _, j = min(cands)
            li, ri = (i, j) if alive[i]["x0"] <= alive[j]["x0"] else (j, i)
            alive[li]["text"] = alive[li]["text"] + alive[ri]["text"]
            alive[li]["x0"] = min(alive[li]["x0"], alive[ri]["x0"])
            alive[li]["y0"] = min(alive[li]["y0"], alive[ri]["y0"])
            alive[li]["x1"] = max(alive[li]["x1"], alive[ri]["x1"])
            alive[li]["y1"] = max(alive[li]["y1"], alive[ri]["y1"])
            alive[li]["cx"] = (alive[li]["x0"] + alive[li]["x1"]) / 2
            alive[li]["cy"] = (alive[li]["y0"] + alive[li]["y1"]) / 2
            alive[ri] = None
            for k in range(len(owner)):
                if owner[k] == ri:
                    owner[k] = li
            changed = True

    keep = [b for b in alive if b]
    remap = {}
    new_idx = 0
    for i, b in enumerate(alive):
        if b:
            remap[i] = new_idx
            new_idx += 1
    new_edges = []
    for a, b, ev in edges:
        ra, rb = remap[owner[a]], remap[owner[b]]
        if ra != rb:
            new_edges.append((ra, rb, ev))
    return keep, new_edges


# ---------------- 构建层级树 ----------------

def build_tree(boxes, edge_list):
    """Preserve directed edges; mark disconnected component roots for review."""
    n = len(boxes)
    if not n:
        raise ValueError("No OCR nodes detected")
    root = min(range(n), key=lambda i: boxes[i]["x0"])
    incoming, accepted, rejected = {}, [], []
    for a, b, evidence in edge_list:
        if not (0 <= a < n and 0 <= b < n) or a == b or b == root:
            rejected.append((a, b, "invalid_edge"))
            continue
        if b in incoming:
            if incoming[b] != a:
                rejected.append((a, b, "multiple_parents"))
            continue
        cursor = a
        while cursor in incoming and cursor != b:
            cursor = incoming[cursor]
        if cursor == b:
            rejected.append((a, b, "cycle"))
            continue
        incoming[b] = a
        accepted.append((a, b, evidence))
    outgoing = {i: [] for i in range(n)}
    for a, b, _ in accepted:
        outgoing[a].append(b)
    detached = [i for i in range(n) if i != root and i not in incoming and outgoing[i]]
    orphans = [i for i in range(n) if i != root and i not in incoming and not outgoing[i]]
    parent = {root: None, **incoming}
    # Display-only attachments remain explicitly uncertain and excluded from chunks.
    for i in detached + orphans:
        parent[i] = root
    from collections import deque
    depth = {root: 0}
    q = deque([root])
    while q:
        u = q.popleft()
        for v in outgoing[u]:
            depth[v] = depth[u] + 1
            q.append(v)
    reachable = len(depth)
    q.extend(detached + orphans)
    for i in q:
        depth[i] = 1
    while q:
        u = q.popleft()
        for v in outgoing[u]:
            depth[v] = depth[u] + 1
            q.append(v)

    children = {i: [] for i in range(n)}
    for v, p in parent.items():
        if p is not None:
            children[p].append(v)
    for u in children:
        children[u].sort(key=lambda k: (boxes[k]["cy"], boxes[k]["cx"]))

    def make(u):
        kids = children[u]
        node = {"id": f"n_{u}", "text": boxes[u]["text"],
                "role": "unknown"}
        if u in orphans:
            node["flag"] = "orphan"
        elif u in detached:
            node["flag"] = "detached"
        if kids:
            node["children"] = [make(k) for k in kids]
        return node

    return {
        "tree_id": f"t_auto_{hashlib.md5(json.dumps(boxes, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:8]}",
        "tree_name": "脑图自动识别结果",
        "root": make(root),
        "_meta": {"n_nodes": n, "n_edges": len(accepted), "edges": accepted,
                  "rejected_edges": rejected, "root_reachable_ratio": reachable / n,
                  "root_child_count": len(outgoing[root]),
                  "root_text": boxes[root]["text"], "depth": depth,
                  "detached": detached, "orphans": orphans, "boxes": boxes},
    }


# ---------------- 可视化 ----------------

def draw_vis(img_bgr, boxes, edge_list, depth, out_fp):
    vis = img_bgr.copy()
    for i, b in enumerate(boxes):
        cv2.rectangle(vis, (b["x0"], b["y0"]), (b["x1"], b["y1"]), (0, 0, 255), 2)
        cv2.putText(vis, f"#{i}d{depth.get(i, -1)}",
                    (b["x0"], max(12, b["y0"] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
    for a, b, _ in edge_list:
        cv2.line(vis, (int(boxes[a]["cx"]), int(boxes[a]["cy"])),
                 (int(boxes[b]["cx"]), int(boxes[b]["cy"])), (0, 200, 0), 3)
    ok, encoded = cv2.imencode(".png", vis)
    if not ok:
        raise ValueError("Cannot encode debug image")
    encoded.tofile(out_fp)
    return out_fp


# ---------------- Markdown 导出 ----------------

def _ascii_tree(node, prefix="", is_last=True, lines=None):
    """树 → └─ 风格文本树。"""
    if lines is None:
        lines = [node["text"]]
    kids = node.get("children", [])
    for k, c in enumerate(kids):
        last = k == len(kids) - 1
        branch = "└─ " if last else "├─ "
        lines.append(prefix + branch + c["text"])
        ext = "   " if last else "│  "
        _ascii_tree(c, prefix + ext, last, lines)
    return lines


def tree_to_markdown(tree, img_path="", json_path=""):
    """识别结果树 → Markdown 文本（统计表 + 层级列表 + 树形图）。"""
    import datetime
    m = tree.get("_meta", {})
    n_nodes = m.get("n_nodes", _count_nodes(tree["root"]))
    root_text = tree["root"]["text"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    md = [f"# 脑图识别结果 · {root_text}", ""]
    md += ["| 项 | 值 |", "|---|---|"]
    md += [f"| 来源图片 | `{img_path}` |" if img_path else "| 来源图片 | - |",
           f"| 识别时间 | {now} |",
           f"| 根节点 | {root_text} |",
           f"| 节点数 / 边数 | {n_nodes} / {m.get('n_edges', '-')} |"]
    if "root_reachable_ratio" in m:
        md += [f"| 根节点真实可达率 | {m['root_reachable_ratio']:.1%} |",
               f"| 真实一级分支数 | {m['root_child_count']} |",
               f"| 待确认断连子树 | {len(m.get('detached', []))} |"]
    if m.get("orphans"):
        md += [f"| 待人工复核(孤块) | {len(m['orphans'])} 个 |"]
    md += [""]

    md += ["## 层级结构", ""]
    def md_list(node, depth=0):
        suffix = " 【连接待确认，以下为独立子树】" if node.get("flag") == "detached" else (
            " 【孤立文字，连接待确认】" if node.get("flag") == "orphan" else "")
        md.append("  " * depth + f"- {node['text']}" + suffix)
        for c in node.get("children", []):
            md_list(c, depth + 1)
    md_list(tree["root"])
    md += [""]

    md += ["## 树形图", "", "```text"]
    md += _ascii_tree(tree["root"])
    md += ["```", ""]

    if m.get("orphans"):
        md += ["## 待人工复核", "",
               "以下文字块未检出任何连线，已默认挂到根节点，建议人工确认：", ""]
        boxes = m.get("boxes", [])
        for i in m["orphans"]:
            t = boxes[i]["text"] if i < len(boxes) else str(i)
            md.append(f"- {t}")
        md += [""]

    if json_path:
        md += ["## 原始数据", "", f"- 树 JSON: `{json_path}`", ""]
    return "\n".join(md)


def _count_nodes(node):
    return 1 + sum(_count_nodes(c) for c in node.get("children", []))


# ---------------- main ----------------



def parse_mindmap_candidate(image_path):
    """Classify by line graph heuristics, then return an atomic tree (no chunking)."""
    image = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Cannot read image")
    boxes, controls = filter_fold_controls(image, ocr_blocks(image))
    negative = {"status": "not_mindmap", "classifier": "directed_line_trace_v2"}
    negative["diagnostics"] = {"n_ocr_nodes": len(boxes), "removed_controls": controls,
                               "boundary_clipped_nodes": [i for i, b in enumerate(boxes)
                                                          if b.get("boundary_clipped")]}
    if len(boxes) < 3:
        return negative
    edges = detect_edges(image, boxes)
    negative["diagnostics"].update(n_nodes=len(boxes), n_edges=len(edges))
    if len(boxes) < 3 or not edges:
        return negative
    tree = build_tree(boxes, edges)
    linked = {i for a, b, _ in edges for i in (a, b)}
    branching = len(tree["root"].get("children", [])) >= 2 or any(
        sum(a == i or b == i for a, b, _ in edges) >= 3
        for i in range(len(boxes)))
    horizontal = max(b["cx"] for b in boxes) - min(b["cx"] for b in boxes) > 40
    coverage = len(linked) / len(boxes)
    diagnostics = {**negative["diagnostics"], "linked_ratio": coverage,
                   "root_reachable_ratio": tree["_meta"]["root_reachable_ratio"],
                   "root_child_count": tree["_meta"]["root_child_count"],
                   "rejected_edges": tree["_meta"]["rejected_edges"],
                   "detached": tree["_meta"]["detached"],
                   "orphans": tree["_meta"]["orphans"],
                   "excess_edges": max(0, len(edges) - len(boxes) + 1)}
    if not (coverage >= 0.8 and branching and horizontal):
        return {**negative, "diagnostics": diagnostics}
    uncertain = bool(diagnostics["rejected_edges"] or diagnostics["boundary_clipped_nodes"] or diagnostics["detached"] or diagnostics["orphans"]
                     or diagnostics["excess_edges"])
    return {"status": "needs_review" if uncertain else "parsed", "image_type": "mindmap",
            "classifier": "directed_line_trace_v2", "needs_review": True,
            "diagnostics": diagnostics,
            "tree": tree, "markdown": tree_to_markdown(tree)}


def export_parse_result(image_path, result, output_dir):
    """Write review artifacts, including status when classification/OCR fails."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source = Path(image_path)
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    prefix = output_dir / f"{source.stem}_{stamp}"
    files = {"result_json": str(prefix) + ".result.json"}
    if result.get("status") in {"parsed", "needs_review"} and result.get("tree"):
        tree = result["tree"]
        files["tree_json"] = str(prefix) + ".tree.json"
        Path(files["tree_json"]).write_text(
            json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
        from knowledge_parsers.mindmap_chunker import chunk_mindmap
        files["chunks_json"] = str(prefix) + ".chunks.json"
        Path(files["chunks_json"]).write_text(json.dumps(
            chunk_mindmap(tree, source_file=str(source)),
            ensure_ascii=False, indent=2), encoding="utf-8")
        files["markdown"] = str(prefix) + ".md"
        Path(files["markdown"]).write_text(
            tree_to_markdown(tree, str(source), files["tree_json"]), encoding="utf-8")
        meta = tree.get("_meta", {})
        if meta.get("boxes"):
            image = cv2.imdecode(np.fromfile(source, dtype=np.uint8), cv2.IMREAD_COLOR)
            edges = meta.get("edges", [])
            files["debug_image"] = str(prefix) + ".debug.png"
            draw_vis(image, meta["boxes"], edges, meta.get("depth", {}), files["debug_image"])
    Path(files["result_json"]).write_text(json.dumps(
        {"image_path": str(source), **result, "output_files": files},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return files
