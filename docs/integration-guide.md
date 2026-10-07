# Integration guide

## Installing the plugin

The plugin lives in `./`. It contains `plugin.json`,
`.mcp.json`, eight skills and five helper agent definitions.

`.mcp.json` launches the stdio server and requires three environment variables:

```
TAXAGENT_TENANT_ID
TAXAGENT_PRINCIPAL_ID
TAXAGENT_ACCOUNTS     comma-separated account ids the principal may read
```

The offline server refuses to start without them. It has no identity provider
and will not infer a principal, because a server that guesses at identity is
worse than one that does not start.

## Replacing the offline identity

For a real deployment, replace `main()` in `gateway/mcp_server.py` so the
principal comes from your identity provider, and serve over an authenticated
remote transport rather than stdio. Everything below the gateway is unchanged:
`ScopeAuthorizer` already treats the principal as the only source of access.

## Binding a custodian connector

Implement `connectors/interface.py::SourcePort` and return `SourceData`. The
contract that matters: declare a real coverage interval per account. A
connector that returns transactions without saying which period it covers makes
every wash-sale screen incomplete, which is the correct outcome but a useless one.

## Binding an optimizer

Implement `optimization/interface.py::OptimizerPort` and add an entry to
`capabilities/engines.yaml` with `status: implemented` and **passing reference
tests**. `test_every_advertised_capability_has_reference_tests` fails the build
otherwise. Report `feasible` unless the engine genuinely proves optimality.

## Adding or updating a tax rule pack

Rules live in `rules/tax/`:

```
parameter-packs/<bundle>.yaml   values, authority, legal_effective_from, knowledge_time
manifests/index.yaml            which bundles exist and which year resolves to which
source-index/README.md          where each authority was read and when last checked
```

To change a rule: add a **new** bundle rather than editing a published one, list
it in the manifest, point the affected years at it, and record what it
supersedes. A published bundle is immutable because historical evidence
packages reference its hash; editing one silently changes what a past run
claims to have applied.

Never put a rule value in `src/`. `test_rule_pack.py::test_no_tax_rule_is_hardcoded_in_source`
fails the build if one reappears in the calculation path.

## Other hosts

`capabilities/host-compatibility.yaml` marks every host but Claude Code as
unverified. Installation manifests, delegation semantics, tool allowlists and
auth behaviour differ; verify per host rather than assuming parity.
