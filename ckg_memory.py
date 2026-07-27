import hashlib
import json
import math
import os
import time
from pathlib import Path


class ContinuousKnowledgeGraph:
    """Persistent structural memory for AST subgraph outcomes."""

    def __init__(self, path=".planning/organic_ckg.json"):
        self.path = Path(path)
        self.entries = {}
        self.calibrations = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        self.entries = data.get("entries", {})
        self.calibrations = data.get("calibrations", {})

    def save(self):
        path = self.path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "entries": dict(sorted(self.entries.items())),
            "calibrations": dict(sorted(self.calibrations.items())),
        }
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp_path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        for attempt in range(6):
            try:
                tmp_path.replace(path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.2 * (attempt + 1))

    def record_calibration(self, signature, apogee_delta, mach_delta=None):
        mach_delta = apogee_delta if mach_delta is None else mach_delta
        entry = self.calibrations.setdefault(
            signature,
            {
                "count": 0,
                "avg_apogee_delta": 1.0,
                "avg_mach_delta": 1.0,
                "min_apogee_delta": None,
                "max_apogee_delta": None,
                "min_mach_delta": None,
                "max_mach_delta": None,
                "last": None,
            },
        )
        if "avg_delta" in entry and "avg_apogee_delta" not in entry:
            entry["avg_apogee_delta"] = entry["avg_delta"]
            entry["avg_mach_delta"] = entry["avg_delta"]
        count = entry["count"]
        old_apogee = entry.get("avg_apogee_delta", 1.0)
        old_mach = entry.get("avg_mach_delta", 1.0)
        entry["avg_apogee_delta"] = (old_apogee * count + apogee_delta) / (count + 1)
        entry["avg_mach_delta"] = (old_mach * count + mach_delta) / (count + 1)
        entry["count"] = count + 1
        entry["min_apogee_delta"] = apogee_delta if entry["min_apogee_delta"] is None else min(entry["min_apogee_delta"], apogee_delta)
        entry["max_apogee_delta"] = apogee_delta if entry["max_apogee_delta"] is None else max(entry["max_apogee_delta"], apogee_delta)
        entry["min_mach_delta"] = mach_delta if entry["min_mach_delta"] is None else min(entry["min_mach_delta"], mach_delta)
        entry["max_mach_delta"] = mach_delta if entry["max_mach_delta"] is None else max(entry["max_mach_delta"], mach_delta)
        entry["last"] = {"apogee_delta": apogee_delta, "mach_delta": mach_delta}

    def iter_entries(self, ast_nodes):
        for key in self.subgraph_hashes(ast_nodes):
            entry = self.entries.get(key)
            if entry:
                yield entry

    def penalty_for(self, ast_nodes):
        penalty = 0.0
        for entry in self.iter_entries(ast_nodes):
            penalty += entry["failures"] * 0.01
            penalty -= entry["successes"] * 0.15
        return max(0.0, penalty)

    def acceptance_multiplier(self, ast_nodes):
        return max(0.05, math.exp(-self.penalty_for(ast_nodes)))

    def acceptance_multiplier_for_items(self, items):
        penalty = 0.0
        for key, _label in items:
            entry = self.entries.get(key)
            if entry:
                penalty += entry["failures"] * 0.01
                penalty -= entry["successes"] * 0.15
        return max(0.05, math.exp(-max(0.0, penalty)))

    def record(self, ast_nodes, score, status, reason=""):
        self.record_items(self.subgraph_items(ast_nodes), score, status, reason)

    def record_items(self, items, score, status, reason=""):
        failed = status != "success"
        for key, label in items:
            entry = self.entries.setdefault(
                key,
                {
                    "label": label,
                    "failures": 0,
                    "successes": 0,
                    "score_total": 0.0,
                    "last_reason": "",
                },
            )
            if failed:
                entry["failures"] += 1
            else:
                entry["successes"] += 1
            entry["score_total"] += float(score)
            entry["last_reason"] = reason

    def record_authority(self, ast_nodes, score, status, reason=""):
        failed = status != "success"
        for key, label in self.authority_subgraph_items(ast_nodes):
            entry = self.entries.setdefault(
                key,
                {
                    "label": label,
                    "failures": 0,
                    "successes": 0,
                    "score_total": 0.0,
                    "last_reason": "",
                },
            )
            if failed:
                entry["failures"] += 1
            else:
                entry["successes"] += 1
            entry["score_total"] += float(score)
            entry["last_reason"] = reason

    def subgraph_hashes(self, ast_nodes):
        return [key for key, _label in self.subgraph_items(ast_nodes)]

    # STAGE/CLOSE_BODY carry no discriminating geometric/physical information
    # (every candidate has exactly one of each per stage, with near-constant
    # params) -- treating them as "subgraph" evidence means a hard mission's
    # normal early-generation failure rate uniformly taxes every future
    # candidate regardless of quality. Empirically this collapsed a fresh
    # 24-pop/4-gen run to 100% ckg_prefilter by generation 4 purely from
    # generation 1's expected low success rate, not from any real learned
    # structural pattern.
    _NON_DISCRIMINATING_NODE_TYPES = {"STAGE", "CLOSE_BODY"}

    def subgraph_items(self, ast_nodes):
        labels = [
            self._node_label(node)
            for node in ast_nodes
            if getattr(node, "node_type", None) not in self._NON_DISCRIMINATING_NODE_TYPES
        ]
        seen = set()
        items = []

        for label in labels:
            self._append_item(items, seen, label)

        for left, right in zip(labels, labels[1:]):
            self._append_item(items, seen, f"{left}->{right}")

        for idx in range(len(labels) - 2):
            triad = "->".join(labels[idx : idx + 3])
            self._append_item(items, seen, triad)

        return items

    def authority_subgraph_items(self, ast_nodes):
        labels = self._authority_stage_labels(ast_nodes)
        seen = set()
        items = []
        for label in labels:
            self._append_item(items, seen, label)
        for left, right in zip(labels, labels[1:]):
            self._append_item(items, seen, f"{left}->{right}")
        return items

    def _append_item(self, items, seen, label):
        digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
        if digest in seen:
            return
        seen.add(digest)
        items.append((digest, label))

    def _node_label(self, node):
        node_type = getattr(node, "node_type", None)
        params = getattr(node, "params", {})
        if node_type is None:
            node_type = node.get("type")
            params = node.get("params", {})

        important = {}
        for key, value in params.items():
            if isinstance(value, float):
                important[key] = round(value, 2)
            else:
                important[key] = value
        return f"{node_type}:{json.dumps(important, sort_keys=True)}"

    def _authority_stage_labels(self, ast_nodes):
        stages = []
        current = []
        for node in ast_nodes:
            node_type = getattr(node, "node_type", None)
            if node_type is None:
                node_type = node.get("type")
            if node_type == "STAGE":
                if current:
                    stages.append(current)
                current = [node]
            elif current:
                current.append(node)
        if current:
            stages.append(current)

        labels = []
        for stage_idx, stage in enumerate(stages):
            params_by_type = {}
            for node in stage:
                node_type = getattr(node, "node_type", None)
                params = getattr(node, "params", {})
                if node_type is None:
                    node_type = node.get("type")
                    params = node.get("params", {})
                params_by_type.setdefault(node_type, []).append(params)

            body = (params_by_type.get("BODY_TUBE") or [{}])[0]
            motor = (params_by_type.get("MOTOR_MOUNT") or [{}])[0]
            fins = params_by_type.get("FIN_SET") or [{}]
            largest_fin = max(
                fins,
                key=lambda item: float(item.get("height", 0.0)) * float(item.get("root", 0.0)),
            )
            labels.append(
                "AUTHORITY_STAGE[{idx}]:motor={motor}|radius={radius:.3f}|length={length:.2f}|"
                "fin_count={fin_count}|fin_root={fin_root:.2f}|fin_height={fin_height:.2f}".format(
                    idx=stage_idx,
                    motor=motor.get("motor_designation", motor.get("motor_index", "unknown")),
                    radius=float(body.get("radius", 0.0)),
                    length=float(body.get("length", 0.0)),
                    fin_count=largest_fin.get("count", "unknown"),
                    fin_root=float(largest_fin.get("root", 0.0)),
                    fin_height=float(largest_fin.get("height", 0.0)),
                )
            )
        return labels
