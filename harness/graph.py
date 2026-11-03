"""可执行决策图（EDG）教学实现：schema 校验、守卫求值、确定性选择。

对应论文 §4.1/§4.2：
- 节点 = (tool_name, 完整字面参数, 前置节点, 公共守卫)；
- 参数封闭由 schema 强制（只允许字符串/整数/列表字面值）；
- 守卫只消费已观察回执（受限结构化表达，不执行任意代码）；
- 资格 = 前置完成 ∧ 守卫为真 ∧ 参数封闭；
- 链式图 + 按创作顺序取第一个合格节点的公开确定性选择规则。
"""

from .config import FROZEN
from .tools import content_hash


class GraphValidationError(Exception):
    pass


GUARD_KEYS = set(FROZEN["guard_allowed_keys"])
TOOL_ARG_SCHEMAS = {
    "run_command": {"required": {"argv"}, "optional": set()},
    "read_file": {"required": {"path"}, "optional": set()},
    "write_file": {"required": {"path", "content"}, "optional": set()},
}


def validate_graph(graph, seen_tool_receipts_hint=None):
    """校验模型创作的图。返回规范化的 graph dict；不合法抛 GraphValidationError。

    注意：本函数绝不补写或修正任何节点/参数；不合法就是不合格，由调用方回退。
    """
    if not isinstance(graph, dict):
        raise GraphValidationError("graph must be an object")
    graph_id = graph.get("graph_id")
    if not isinstance(graph_id, str) or not graph_id.strip():
        raise GraphValidationError("graph_id missing")
    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not (1 <= len(nodes) <= FROZEN["max_graph_nodes"]):
        raise GraphValidationError("nodes must be a non-empty list (max %d)"
                                   % FROZEN["max_graph_nodes"])

    norm_nodes = []
    seen_ids = set()
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise GraphValidationError("node %d must be an object" % i)
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise GraphValidationError("node %d: node_id missing" % i)
        if node_id in seen_ids:
            raise GraphValidationError("duplicate node_id: %s" % node_id)
        seen_ids.add(node_id)

        tool_name = node.get("tool_name")
        if tool_name not in TOOL_ARG_SCHEMAS:
            raise GraphValidationError("node %s: tool_name not allowed: %r" % (node_id, tool_name))

        args = node.get("args")
        if not isinstance(args, dict):
            raise GraphValidationError("node %s: args must be an object with complete "
                                       "literal values (no placeholders)" % node_id)
        schema = TOOL_ARG_SCHEMAS[tool_name]
        keys = set(args.keys())
        if keys != schema["required"] | schema["optional"]:
            raise GraphValidationError(
                "node %s: args keys %s != required %s" % (node_id, sorted(keys), sorted(schema["required"])))
        for k, v in args.items():
            if not (isinstance(v, str) or isinstance(v, int) or isinstance(v, list)):
                raise GraphValidationError("node %s: args.%s must be a literal "
                                           "(str/int/list), got %r" % (node_id, k, type(v).__name__))
            if isinstance(v, list) and not all(isinstance(x, str) for x in v):
                raise GraphValidationError("node %s: args.%s list items must be strings" % (node_id, k))
        if tool_name == "run_command":
            argv = args.get("argv")
            if (not isinstance(argv, list) or not argv
                    or not all(isinstance(x, str) and x.strip() for x in argv)):
                raise GraphValidationError(
                    "node %s: run_command args.argv must be a non-empty list of "
                    "literal strings, got %r" % (node_id, argv))

        depends_on = node.get("depends_on")
        if depends_on == "root":
            pass
        elif isinstance(depends_on, str) and depends_on in seen_ids:
            pass
        else:
            raise GraphValidationError(
                "node %s: depends_on must be 'root' or an earlier node id, got %r"
                % (node_id, depends_on))

        guard = node.get("guard")
        if not isinstance(guard, dict) or not guard:
            raise GraphValidationError("node %s: guard must be a non-empty object" % node_id)
        for gk, gv in guard.items():
            if gk not in GUARD_KEYS:
                raise GraphValidationError(
                    "node %s: guard key %r not in allowed %s" % (node_id, gk, sorted(GUARD_KEYS)))
            if gk == "exit_code":
                if not isinstance(gv, int):
                    raise GraphValidationError("node %s: guard.exit_code must be int" % node_id)
            else:
                if not isinstance(gv, str) or not gv:
                    raise GraphValidationError("node %s: guard.%s must be non-empty str" % (node_id, gk))

        norm_nodes.append({
            "node_id": node_id,
            "tool_name": tool_name,
            "args": args,
            "depends_on": depends_on,
            "guard": guard,
            "order": i,
        })

    return {
        "graph_id": graph_id.strip(),
        "rationale": str(graph.get("rationale", ""))[:2000],
        "nodes": norm_nodes,
    }


def evaluate_guard(guard, receipt):
    """对照真实回执求值守卫。

    返回 (result, reason)；result ∈ {"pass", "failed", "unknown"}。
    只消费回执里已经存在的字段；绝不做预测或推断。
    """
    reasons = []
    result = "pass"
    for key, want in guard.items():
        if key == "exit_code":
            got = receipt.get("exit_code")
            if got is None:
                return "unknown", "receipt has no exit_code"
            if got == want:
                reasons.append("exit_code==%d ok" % want)
            else:
                reasons.append("exit_code got %r, guard wants %r" % (got, want))
                result = "failed"
        elif key in ("stdout_contains", "stderr_contains"):
            field = key.split("_")[0]  # stdout / stderr
            text = receipt.get(field)
            if text is None:
                return "unknown", "receipt has no %s" % field
            if want in text:
                reasons.append("%s contains %r ok" % (field, want))
            else:
                reasons.append("%s does not contain %r" % (field, want))
                result = "failed"
    return result, "; ".join(reasons)


def graph_hash(graph):
    return content_hash(graph)


def next_eligible_node(graph, node_status, receipts):
    """公开确定性选择规则：按创作顺序，取第一个
    （前置完成 ∧ 守卫为真 ∧ 参数已封闭）的未执行节点。

    返回 (node, None) 或 (None, reason)；
    reason 说明为何没有合格节点（守卫失败/未知/等待前置/耗尽）。
    """
    for node in graph["nodes"]:
        nid = node["node_id"]
        if node_status.get(nid) == "executed":
            continue
        pred = node["depends_on"]
        if pred == "root":
            pred_receipt = receipts.get("__root__")
        else:
            if node_status.get(pred) != "executed":
                return None, "waiting: predecessor %s not completed" % pred
            pred_receipt = receipts.get(pred)
        if pred_receipt is None:
            return None, "waiting: predecessor receipt missing"
        result, reason = evaluate_guard(node["guard"], pred_receipt)
        if result == "pass":
            return node, None
        if result == "failed":
            return None, "guard_failed at %s: %s" % (nid, reason)
        return None, "guard_unknown at %s: %s" % (nid, reason)
    return None, "graph_exhausted"
