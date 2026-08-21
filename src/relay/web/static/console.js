function relayConsole() {
  return {
    sandbox: null,
    endpoints: [],
    destinationChoice: "/mock/always-200",
    customUrl: "",
    lastTrigger: null,
    timeline: [],
    inspected: null,
    metrics: {},
    dlq: [],
    verify: { secret: "", body: "", timestamp: "", signature: "", result: null },
    loading: { sandbox: false, endpoint: false, event: false },
    errors: { sandbox: null, endpoint: null },
    _eventSource: null,
    _metricsTimer: null,

    init() {
      // Nothing to provision until the visitor clicks "Start sandbox" -- no auto-start,
      // so an idle tab never spends a slot from the per-IP sandbox-creation limiter.
    },

    async _api(path, options = {}) {
      const headers = Object.assign({}, options.headers || {});
      if (this.sandbox) headers["X-API-Key"] = this.sandbox.api_key;
      if (options.body) headers["Content-Type"] = "application/json";
      const response = await fetch(path, Object.assign({}, options, { headers }));
      const text = await response.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch (e) {
        data = text;
      }
      if (!response.ok) {
        const detail = (data && data.detail) || response.statusText;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      return { data, response };
    },

    async startSandbox() {
      this.loading.sandbox = true;
      this.errors.sandbox = null;
      try {
        const { data } = await this._api("/v1/sandbox", { method: "POST" });
        this.sandbox = data;
        this._connectStream();
        this._pollMetrics();
        await this.loadDlq();
      } catch (e) {
        this.errors.sandbox = e.message;
      } finally {
        this.loading.sandbox = false;
      }
    },

    async registerEndpoint() {
      this.loading.endpoint = true;
      this.errors.endpoint = null;
      try {
        const url =
          this.destinationChoice === "custom"
            ? this.customUrl
            : window.location.origin + this.destinationChoice;
        const { data } = await this._api("/v1/endpoints", {
          method: "POST",
          body: JSON.stringify({ url, subscribed_event_types: ["demo.triggered"] }),
        });
        this.endpoints.push(data);
        this.verify.secret = data.secret;
      } catch (e) {
        this.errors.endpoint = e.message;
      } finally {
        this.loading.endpoint = false;
      }
    },

    async triggerEvent(endpoint) {
      this.loading.event = true;
      try {
        const idempotencyKey = crypto.randomUUID();
        const { data, response } = await this._api("/v1/events", {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: JSON.stringify({
            type: "demo.triggered",
            payload: { endpoint_id: endpoint.id, at: new Date().toISOString() },
          }),
        });
        this.lastTrigger = {
          status: response.status + " " + response.statusText,
          location: response.headers.get("Location"),
          event: data,
        };
      } catch (e) {
        this.lastTrigger = { status: "error", location: e.message };
      } finally {
        this.loading.event = false;
      }
    },

    _connectStream() {
      const url = "/v1/sandbox/stream?api_key=" + encodeURIComponent(this.sandbox.api_key);
      this._eventSource = new EventSource(url);
      this._eventSource.onmessage = (event) => {
        const attempt = JSON.parse(event.data);
        this.timeline.unshift(attempt);
        if (this.timeline.length > 50) this.timeline.pop();
        const ep = this.endpoints.find((e) => e.id === attempt.endpoint_id);
        if (ep) ep.breaker_state = attempt.breaker_state;
      };
    },

    inspect(attempt) {
      this.inspected = attempt;
    },

    _pollMetrics() {
      const tick = async () => {
        if (!this.sandbox) return;
        try {
          const { data } = await this._api("/v1/sandbox/metrics");
          this.metrics = data;
        } catch (e) {
          /* best-effort */
        }
      };
      tick();
      this._metricsTimer = setInterval(tick, 3000);
    },

    async loadDlq() {
      try {
        const { data } = await this._api("/v1/dlq");
        this.dlq = data.items;
      } catch (e) {
        /* best-effort */
      }
    },

    async replay(delivery) {
      try {
        await this._api(`/v1/deliveries/${delivery.id}/replay`, { method: "POST" });
        await this.loadDlq();
      } catch (e) {
        /* surfaced via the DLQ list not refreshing */
      }
    },

    async verifySignature() {
      try {
        const { data } = await this._api("/v1/sandbox/verify-signature", {
          method: "POST",
          body: JSON.stringify({
            secret: this.verify.secret,
            body: this.verify.body,
            timestamp: Number(this.verify.timestamp),
            signature: this.verify.signature,
          }),
        });
        this.verify.result = data.valid;
      } catch (e) {
        this.verify.result = false;
      }
    },
  };
}
