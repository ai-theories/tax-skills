"""Render a review brief from an evidence package only.

The renderer never receives the ledger or the snapshot; if a number is not in
the evidence package it cannot appear in the report.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .package_builder import resolve_claim


def render_review_brief(package: Dict[str, Any]) -> str:
    lines: List[str] = []
    scope = package["scope"]
    lines.append("# Tax analysis review brief")
    lines.append("")
    lines.append(f"Run `{package['run_id']}` — analysis only, not authorized for execution.")
    lines.append("")
    lines.append("## Scope")
    lines.append(f"- Subject: `{scope['subject_id']}`, as of {scope['as_of']}")
    lines.append(f"- Accounts included: {', '.join(scope['accounts_included']) or 'none'}")
    if scope["accounts_missing_basis"]:
        lines.append(f"- Accounts with missing basis: {', '.join(scope['accounts_missing_basis'])}")
    lines.append("")

    lines.append("## Result")
    for claim in ("trade_count", "cash_raised", "harvested_losses", "net_realized_gain",
                  "short_term_gain", "long_term_gain", "gain_budget_residual",
                  "disallowed_loss", "wash_sale_status"):
        value = resolve_claim(package, claim)
        if value is not None:
            lines.append(f"- {claim.replace('_', ' ')}: {value}")
    lines.append("")

    diagnostics = (package["results"].get("candidate") or {}).get("diagnostics", {})
    gap = diagnostics.get("optimality_gap")
    if gap:
        lines.append("## Coordination")
        lines.append(f"- Method: {diagnostics.get('coordination', 'n/a')}, "
                     f"order: {', '.join(diagnostics.get('allocation_order', []))}")
        lines.append(f"- Order rule: {diagnostics.get('order_rule', 'n/a')}")
        if gap["provably_optimal"]:
            lines.append("- No reallocation of this budget raises more cash: this "
                         "allocation is optimal for the relaxed problem.")
        else:
            lines.append(f"- Upper bound on cash from the same budget: "
                         f"{gap['bound_cash']} (achieved {gap['achieved_cash']}), "
                         f"a ceiling of {gap['gap']} ({gap['gap_percent']}) on what a "
                         f"joint optimizer could add.")
            lines.append(f"- {gap['interpretation']}")
        lines.append("")

    validation = package["results"]["validation"]
    lines.append("## Validation")
    for check, status in sorted(validation["checks"].items()):
        lines.append(f"- {check}: {status}")
    if validation["violations"]:
        lines.append("")
        lines.append("### Violations")
        for violation in validation["violations"]:
            lines.append(f"- `{violation['code']}` {violation['message']}")
    lines.append("")

    if package["limitations"]:
        lines.append("## Limitations")
        # Two findings with the same code are two different accounts. Printing
        # the message alone leaves a reviewer unable to tell which, which is
        # the one thing these entries exist to say.
        seen = set()
        for limitation in package["limitations"]:
            refs = limitation.get("refs") or {}
            key = (limitation["code"], tuple(sorted(refs.items())))
            if key in seen:
                continue
            seen.add(key)
            subject = (refs.get("account_id") or refs.get("lot_id")
                       or refs.get("monitor_until") or "")
            suffix = f" ({subject})" if subject else ""
            lines.append(f"- `{limitation['code']}`{suffix} {limitation['message']}")
        lines.append("")

    if package["assumptions"]:
        lines.append("## Assumptions")
        for assumption in package["assumptions"]:
            source = assumption.get("source", "unspecified")
            lines.append(f"- {assumption.get('field')}: {assumption.get('value')} (source: {source})")
        lines.append("")

    manifest = package["engine_manifest"]
    lines.append("## Reproducibility")
    lines.append(f"- Snapshot: `{package['inputs']['snapshot_ref']}` ({package['inputs']['snapshot_hash'][:19]}…)")
    lines.append(f"- Engine: {manifest['optimizer']['name'] or 'n/a'} {manifest['optimizer']['version']}")
    lines.append(f"- Tax rules: `{manifest.get('rule_bundle_ref') or 'UNPINNED'}` "
                 f"({(manifest.get('rule_bundle_hash') or '')[:19]}…)")
    lines.append(f"- Package: taxagent {manifest['package_version']}, Python {manifest['python_version']}")
    lines.append(f"- Evidence hash: `{package['content_hash'][:19]}…`")
    lines.append("")
    lines.append("Prepared for professional review. This is not tax advice and no trade is authorized.")
    return "\n".join(lines)
