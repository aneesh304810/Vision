const vscode = require('vscode');

class ResultPanel {
  /**
   * Show the result in a side-by-side webview panel.
   *
   * @param {vscode.ExtensionContext} context
   * @param {string} content  - raw text / code returned by the model
   * @param {{
   *   editor?: vscode.TextEditor,
   *   selection?: vscode.Selection,
   *   insertable?: boolean,
   *   replaceSelection?: boolean,
   *   label?: string
   * }} opts
   */
  static show(context, content, opts = {}) {
    const panel = vscode.window.createWebviewPanel(
      'qwenResult',
      `Qwen — ${opts.label ?? 'Result'}`,
      vscode.ViewColumn.Beside,
      { enableScripts: true }
    );

    // Strip surrounding markdown code fences if model added them
    const cleaned = stripFences(content);

    panel.webview.html = buildHtml(cleaned, opts);

    // Handle messages from the webview
    panel.webview.onDidReceiveMessage(
      async (message) => {
        switch (message.command) {
          case 'insert': {
            const editor = opts.editor ?? vscode.window.activeTextEditor;
            if (!editor) {
              vscode.window.showWarningMessage('No active editor to insert into.');
              return;
            }
            await editor.edit((editBuilder) => {
              if (opts.replaceSelection && opts.selection && !opts.selection.isEmpty) {
                editBuilder.replace(opts.selection, cleaned);
              } else {
                editBuilder.insert(editor.selection.active, cleaned);
              }
            });
            panel.dispose();
            break;
          }
          case 'copy': {
            await vscode.env.clipboard.writeText(cleaned);
            vscode.window.showInformationMessage('Copied to clipboard.');
            break;
          }
        }
      },
      undefined,
      context.subscriptions
    );
  }
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function stripFences(text) {
  // Remove leading ```lang and trailing ```
  return text
    .replace(/^```[\w]*\n?/, '')
    .replace(/\n?```$/, '')
    .trim();
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function buildHtml(content, opts) {
  const insertLabel = opts.replaceSelection ? 'Replace Selection' : 'Insert at Cursor';
  const showInsert = opts.insertable !== false;

  return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Qwen Result</title>
  <style>
    :root {
      --bg: var(--vscode-editor-background);
      --fg: var(--vscode-editor-foreground);
      --border: var(--vscode-editorWidget-border, #444);
      --btn-bg: var(--vscode-button-background);
      --btn-fg: var(--vscode-button-foreground);
      --btn-hover: var(--vscode-button-hoverBackground);
      --code-bg: var(--vscode-textCodeBlock-background, #1e1e1e);
      --font-mono: var(--vscode-editor-font-family, 'Cascadia Code', monospace);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--fg);
      font-family: var(--vscode-font-family, sans-serif);
      font-size: var(--vscode-font-size, 13px);
      padding: 16px;
      display: flex;
      flex-direction: column;
      height: 100vh;
      gap: 12px;
    }
    h2 {
      font-size: 13px;
      font-weight: 600;
      opacity: 0.7;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }
    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    button {
      background: var(--btn-bg);
      color: var(--btn-fg);
      border: none;
      padding: 6px 14px;
      border-radius: 3px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
    }
    button:hover { background: var(--btn-hover); }
    button.secondary {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--fg);
    }
    button.secondary:hover { background: rgba(255,255,255,0.05); }
    .code-wrap {
      flex: 1;
      overflow: auto;
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 4px;
    }
    pre {
      margin: 0;
      padding: 16px;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: var(--font-mono);
      font-size: 13px;
      line-height: 1.6;
    }
  </style>
</head>
<body>
  <h2>${escapeHtml(opts.label ?? 'Result')}</h2>
  <div class="toolbar">
    ${showInsert ? `<button id="insertBtn">${escapeHtml(insertLabel)}</button>` : ''}
    <button class="secondary" id="copyBtn">Copy</button>
  </div>
  <div class="code-wrap">
    <pre id="code">${escapeHtml(content)}</pre>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    ${showInsert ? `
    document.getElementById('insertBtn').addEventListener('click', () => {
      vscode.postMessage({ command: 'insert' });
    });` : ''}
    document.getElementById('copyBtn').addEventListener('click', () => {
      vscode.postMessage({ command: 'copy' });
    });
  </script>
</body>
</html>`;
}

module.exports = { ResultPanel };
