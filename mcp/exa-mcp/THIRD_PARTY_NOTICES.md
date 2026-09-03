# Third-party notices

This service is a thin passthrough proxy. Unlike the sibling MCP services in this
repository, it bundles no third-party binary and vendors no upstream documentation.

- Every tool exposed here is served by Exa's own hosted MCP server at
  <https://mcp.exa.ai/mcp>. Tool names, descriptions, and input schemas are fetched
  from that server at request time and forwarded verbatim; none of them are
  redeclared locally.
- Use of those tools is governed by Exa's terms of service and the API key this
  deployment is configured with. See <https://exa.ai/docs/reference/exa-mcp>.
- Exa's MCP server is open source (MIT) at <https://github.com/exa-labs/exa-mcp-server>.
  This repository does not include or modify its code.

The MCP wrapper code in this directory is licensed under MIT and is independent of
Exa's service and client.
