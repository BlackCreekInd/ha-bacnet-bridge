// Sidebar panel for BACnet Bridge. Plain custom element, no build step.
// Home Assistant sets .hass, .narrow and .panel on it.

const REFRESH_MS = 10000;

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const ABBR = {
  "analog-input": "AI", "analog-output": "AO", "analog-value": "AV",
  "binary-input": "BI", "binary-output": "BO", "binary-value": "BV",
  "multi-state-input": "MSI", "multi-state-output": "MSO", "multi-state-value": "MSV",
};
const objLabel = (oid) => {
  const [type, inst] = String(oid).split(",");
  return `${ABBR[type] || type} ${inst}`;
};

class BacnetBridgePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._status = null;
    this._error = null;
    this._timer = null;
    this._built = false;
  }

  set hass(hass) {
    const old = this._hass;
    this._hass = hass;
    if (!this._built) this._build();
    this._menu.hass = hass;
    if (!old) this._load();
    else if (this._entityIds().some((id) => old.states[id] !== hass.states[id])) this._render();
  }

  _entityIds() {
    const s = this._status;
    if (!s?.loaded) return [];
    return [
      ...s.devices.flatMap((d) => d.objects.map((o) => o.entity_id).filter(Boolean)),
      ...s.published.map((p) => p.entity_id),
    ];
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  connectedCallback() {
    if (this._hass) this._load();
    this._timer = setInterval(() => this._load(), REFRESH_MS);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  async _load() {
    if (!this._hass) return;
    try {
      this._status = await this._hass.callWS({ type: "bacnet_bridge/status" });
      this._error = null;
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._render();
  }

  _build() {
    this._built = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color); font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
        .toolbar { display: flex; align-items: center; gap: 8px; height: var(--header-height, 56px); padding: 0 12px; background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff); font-size: 20px; }
        .toolbar .title { flex: 1; }
        .toolbar a { color: inherit; font-size: 14px; text-decoration: none; border: 1px solid currentColor; border-radius: 4px; padding: 4px 10px; }
        .content { padding: 16px; max-width: 1100px; margin: 0 auto; display: grid; gap: 16px; }
        .card { background: var(--card-background-color, #fff); border-radius: var(--ha-card-border-radius, 12px); box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); padding: 16px; overflow-x: auto; }
        h2 { margin: 0 0 8px; font-size: 18px; font-weight: 500; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .meta { color: var(--secondary-text-color); font-size: 13px; margin-bottom: 8px; }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--divider-color, #e0e0e0); white-space: nowrap; }
        th { color: var(--secondary-text-color); font-weight: 500; }
        td.name { white-space: normal; }
        .badge { font-size: 12px; padding: 2px 8px; border-radius: 10px; color: #fff; }
        .ok { background: var(--success-color, #43a047); }
        .bad { background: var(--error-color, #db4437); }
        .muted { color: var(--secondary-text-color); }
        a.entity { color: var(--primary-color); cursor: pointer; text-decoration: none; }
        .err { color: var(--error-color, #db4437); }
      </style>
      <div class="toolbar">
        <ha-menu-button></ha-menu-button>
        <div class="title">HA-BACnet Bridge</div>
        <a href="/config/integrations/integration/bacnet_bridge">Configure</a>
      </div>
      <div class="content" id="content"><div class="card muted">Loading…</div></div>`;
    this._menu = this.shadowRoot.querySelector("ha-menu-button");
    this._menu.narrow = this._narrow;
    this.shadowRoot.getElementById("content").addEventListener("click", (ev) => {
      const link = ev.target.closest("a.entity");
      if (!link) return;
      ev.preventDefault();
      this.dispatchEvent(new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId: link.dataset.entity } }));
    });
  }

  _state(entityId) {
    const st = entityId && this._hass?.states[entityId];
    if (!st) return '<span class="muted">—</span>';
    const text = this._hass.formatEntityState ? this._hass.formatEntityState(st) : `${st.state} ${st.attributes.unit_of_measurement || ""}`;
    return esc(text);
  }

  _entity(entityId) {
    if (!entityId) return '<span class="muted">not created</span>';
    return `<a class="entity" href="#" data-entity="${esc(entityId)}">${esc(entityId)}</a>`;
  }

  _render() {
    const root = this.shadowRoot.getElementById("content");
    if (!root) return;
    const s = this._status;
    if (this._error && !s) {
      root.innerHTML = `<div class="card err">Could not load status: ${esc(this._error)}</div>`;
      return;
    }
    if (!s) return;
    if (!s.loaded) {
      root.innerHTML = `<div class="card">BACnet Bridge is not running. Check it under <a href="/config/integrations/integration/bacnet_bridge">Settings → Devices &amp; services</a>.</div>`;
      return;
    }

    const local = s.local;
    let html = `
      <div class="card">
        <h2>${esc(local.name)} <span class="muted">· BACnet device ${esc(local.instance)}</span></h2>
        <div class="meta">${[local.model, local.location, local.version && `v${local.version}`].filter(Boolean).map(esc).join(" · ")}</div>
        <div class="meta">Interface ${esc(local.interface)} · UDP ${esc(local.port)} · ${s.devices.length} remote device(s) · ${s.published.length} published entit${s.published.length === 1 ? "y" : "ies"}</div>
        ${this._error ? `<div class="err">Last refresh failed: ${esc(this._error)}</div>` : ""}
      </div>`;

    if (!s.devices.length) {
      html += `<div class="card"><h2>Remote devices</h2><div class="muted">None yet. Click <b>Configure</b> → <i>Discover and add a BACnet device</i>.</div></div>`;
    }
    for (const d of s.devices) {
      const sub = [d.address, d.vendor, d.model, d.scan_interval ? `poll ${d.scan_interval}s` : null].filter(Boolean).map(esc).join(" · ");
      html += `
        <div class="card">
          <h2>${esc(d.name)} <span class="muted">· device ${esc(d.instance)}</span>
            <span class="badge ${d.online ? "ok" : "bad"}">${d.online ? "online" : "offline"}</span></h2>
          <div class="meta">${sub}${d.error && !d.online ? ` · <span class="err">${esc(d.error)}</span>` : ""}</div>
          <table>
            <thead><tr><th>Object</th><th>Name</th><th>BACnet value</th><th>Entity</th><th>HA state</th><th></th></tr></thead>
            <tbody>
              ${d.objects.map((o) => `
                <tr>
                  <td>${esc(objLabel(o.object_id))}</td>
                  <td class="name">${esc(o.name)}</td>
                  <td>${o.value === null ? '<span class="muted">—</span>' : esc(o.value)}</td>
                  <td>${this._entity(o.entity_id)}</td>
                  <td>${this._state(o.entity_id)}</td>
                  <td class="muted">${o.writable ? "writable" : ""}</td>
                </tr>`).join("")}
            </tbody>
          </table>
        </div>`;
    }

    html += `<div class="card"><h2>Published to BACnet</h2>`;
    if (!s.published.length) {
      html += `<div class="muted">No entities published. Click <b>Configure</b> → <i>Publish Home Assistant entities</i>.</div>`;
    } else {
      html += `
        <table>
          <thead><tr><th>BACnet object</th><th>Entity</th><th>Name</th><th>State</th></tr></thead>
          <tbody>
            ${s.published.map((p) => `
              <tr>
                <td>${esc(objLabel(p.object_id))}</td>
                <td>${this._entity(p.entity_id)}</td>
                <td class="name">${esc(this._hass.states[p.entity_id]?.attributes.friendly_name || "")}</td>
                <td>${this._state(p.entity_id)}</td>
              </tr>`).join("")}
          </tbody>
        </table>`;
    }
    html += `</div>`;
    root.innerHTML = html;
  }
}

if (!customElements.get("bacnet-bridge-panel")) {
  customElements.define("bacnet-bridge-panel", BacnetBridgePanel);
}
