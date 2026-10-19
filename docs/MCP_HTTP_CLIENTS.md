# HTTP clients: n8n and Apps Script

The MCP endpoint is `/mcp`. It uses Streamable HTTP with stateless requests and one JSON response per POST (`FastMCP(stateless_http=True, json_response=True)`). No `initialize` request or session ID is needed. The bearer token is validated on every POST. Tool handlers use no `ctx.session` or other state carried between calls.

## curl: one authenticated tool call

Replace the URL, token, and Odoo instance with values configured for the deployment and user.

```sh
export MCP_URL='https://mcp.example.test/mcp'
export MCP_TOKEN='replace-with-a-user-token'

curl --fail-with-body --silent --show-error "$MCP_URL" \
  -H "Authorization: Bearer $MCP_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-11-25' \
  --data '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "search_pages",
      "arguments": {"instance": "yfokus", "query": "news", "limit": 20}
    }
  }'
```

The response body is a JSON-RPC envelope. A successful tool result is in `result`; tool-level failures set `result.isError` to `true`.

## Google Apps Script: `UrlFetchApp`

Store the MCP URL and bearer token in Script Properties. This function makes one POST and returns the MCP `result` object.

```javascript
function searchPages() {
  const properties = PropertiesService.getScriptProperties();
  const url = properties.getProperty('MCP_URL');
  const token = properties.getProperty('MCP_TOKEN');
  const payload = {
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/call',
    params: {
      name: 'search_pages',
      arguments: {instance: 'yfokus', query: 'news', limit: 20}
    }
  };
  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/json, text/event-stream',
      'MCP-Protocol-Version': '2025-11-25'
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });

  const status = response.getResponseCode();
  const body = JSON.parse(response.getContentText());
  if (status < 200 || status >= 300) {
    throw new Error('MCP HTTP ' + status + ': ' + response.getContentText());
  }
  if (body.error) {
    throw new Error('MCP ' + body.error.code + ': ' + body.error.message);
  }
  if (body.result && body.result.isError) {
    throw new Error('MCP tool call failed: ' + JSON.stringify(body.result.content));
  }
  return body.result;
}
```

## n8n MCP Client

Configure the MCP Client node with the Streamable HTTP URL ending in `/mcp` and a bearer token in its authorization settings. The node manages the MCP request format; no session setup is required by this server. The HTTP endpoint can also be called directly by n8n's HTTP Request node using the same JSON body and headers shown above.
