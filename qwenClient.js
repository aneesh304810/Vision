const https = require('https');
const http = require('http');
const { URL } = require('url');

class QwenClient {
  /**
   * @param {{ baseUrl: string, model: string, maxTokens: number, temperature: number }} opts
   */
  constructor(opts) {
    this.baseUrl = (opts.baseUrl || 'http://localhost:8080').replace(/\/$/, '');
    this.model = opts.model || 'qwen2.5-coder';
    this.maxTokens = opts.maxTokens ?? 2048;
    this.temperature = opts.temperature ?? 0.2;
  }

  /**
   * Send a chat completion request.
   * llama.cpp server exposes an OpenAI-compatible /v1/chat/completions endpoint.
   *
   * @param {string} systemPrompt
   * @param {string} userPrompt
   * @returns {Promise<string>} assistant reply text
   */
  async chat(systemPrompt, userPrompt) {
    const body = JSON.stringify({
      model: this.model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      max_tokens: this.maxTokens,
      temperature: this.temperature,
      stream: false,
    });

    const raw = await this._post('/v1/chat/completions', body);
    const json = JSON.parse(raw);

    // Extract text from standard OpenAI response shape
    const content = json?.choices?.[0]?.message?.content;
    if (!content) {
      throw new Error(`Unexpected response shape: ${raw.slice(0, 200)}`);
    }
    return content.trim();
  }

  /**
   * Low-level HTTP POST — uses Node's built-in http/https, no extra deps.
   * @param {string} path
   * @param {string} body  JSON string
   * @returns {Promise<string>}
   */
  _post(path, body) {
    return new Promise((resolve, reject) => {
      const url = new URL(this.baseUrl + path);
      const isHttps = url.protocol === 'https:';
      const lib = isHttps ? https : http;

      const options = {
        hostname: url.hostname,
        port: url.port || (isHttps ? 443 : 80),
        path: url.pathname + url.search,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
        timeout: 120_000, // 2 min — local models can be slow
      };

      const req = lib.request(options, (res) => {
        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => {
          const raw = Buffer.concat(chunks).toString('utf8');
          if (res.statusCode >= 400) {
            return reject(
              new Error(`HTTP ${res.statusCode}: ${raw.slice(0, 300)}`)
            );
          }
          resolve(raw);
        });
      });

      req.on('error', reject);
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Request timed out after 120s — is llama.cpp running?'));
      });

      req.write(body);
      req.end();
    });
  }
}

module.exports = { QwenClient };
