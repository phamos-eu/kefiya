// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt
//
// The stylesheet of the outgoing-payments list, and nothing else.
//
// It sat in payment_outbox.js, which is the list's behaviour: 55 lines of CSS
// in the middle of a file that is already too long, and coupled to none of it.

frappe.provide("kefiya");

// The look travels with the list. It used to sit in the block's style field,
// one database row away from the markup it applies to, which is how the two
// drift apart: a class renamed here stayed styled there until somebody
// noticed.
//
// Where it goes is not a detail. A Custom HTML Block renders inside a SHADOW
// ROOT -- that is the whole reason the block has a style field of its own,
// and why its script is handed a `root_element` instead of reaching for
// document. A stylesheet appended to document.head does not cross that
// boundary: the page came up completely unstyled, every rule silently
// matching nothing.
//
// So it is appended to the node the list actually lives in. getRootNode()
// answers the shadow root inside a block and the document outside one, so the
// same function is right in both. Next to #zk rather than inside it: render()
// rewrites that element's innerHTML on every draw and would take the
// stylesheet with it.
kefiya.outbox_style = function (root) {
	const home = (root && root.getRootNode && root.getRootNode()) || document;
	const holder = home === document ? document.head : home;
	if (!holder || holder.querySelector("#kefiya-outbox-style")) return;
	const el = document.createElement("style");
	el.id = "kefiya-outbox-style";
	el.textContent = [
		"#zk{font-size:13px;color:var(--text-color)}",
		"#zk .zk-load,#zk .zk-empty{padding:24px;color:var(--text-muted)}",
		"#zk .zk-err{padding:16px;background:#FBEDED;color:#A32D2D;",
		"border-radius:6px}",
		"#zk .zk-bar{display:flex;gap:8px;align-items:center;",
		"flex-wrap:wrap;margin-bottom:10px}",
		"#zk .zk-bar input[data-act='q']{flex:1;min-width:240px;padding:5px 8px;",
		"border:1px solid var(--border-color);border-radius:5px;font-size:13px}",
		"#zk button{padding:5px 10px;border:1px solid var(--border-color);",
		"background:var(--card-bg);border-radius:5px;cursor:pointer;",
		"font-size:12px}",
		"#zk button:hover:not([disabled]){background:var(--fg-hover-color)}",
		"#zk button[disabled]{opacity:.45;cursor:not-allowed}",
		"#zk .zk-primary,#zk .zk-send{background:#14532d;color:#fff;",
		"border-color:#14532d;font-weight:600}",
		// The generic hover above painted the green buttons in the page's
		// light hover colour, with their white text still on: the Send
		// button went white on white under the mouse. Their own hover
		// keeps the text readable -- a darker green, not a lighter grey.
		"#zk .zk-primary:hover:not([disabled]),#zk .zk-send:hover:not([disabled])"
			+ "{background:#0f3f22;border-color:#0f3f22;color:#fff}",
		"#zk .zk-send{padding:8px 22px;font-size:14px}",
		"#zk .zk-send[disabled]{background:#c8d3cb;border-color:#c8d3cb;",
		"color:#fff;opacity:1}",
		"#zk .zk-chk{font-size:12px;color:var(--text-muted);white-space:nowrap}",
		"#zk .zk-tb{width:100%;border-collapse:collapse}",
		"#zk .zk-tb th{text-align:left;font-weight:600;font-size:11px;",
		"color:var(--text-muted);border-bottom:2px solid var(--border-color);",
		"padding:6px}",
		"#zk .zk-tb th.zk-sortable{cursor:pointer;user-select:none}",
		"#zk .zk-tb th.zk-sortable:hover{color:var(--text-color)}",
		"#zk .zk-tb td{padding:7px 6px;",
		"border-bottom:1px solid var(--border-color);vertical-align:top}",
		"#zk .zk-tb .r{text-align:right;white-space:nowrap}",
		"#zk .zk-cx{width:26px}",
		"#zk tr.zk-row{cursor:pointer}",
		"#zk tr.zk-row:hover{background:var(--fg-hover-color)}",
		"#zk tr.zk-row:focus{outline:2px solid var(--primary);",
		"outline-offset:-2px}",
		"#zk .zk-sub{color:var(--text-muted);font-size:11px}",
		"#zk .zk-nodoc{color:var(--text-muted);opacity:.6}",
		"#zk .zk-doc{white-space:nowrap;font-size:12px}",
		"#zk .zk-iban{font-family:ui-monospace,Menlo,monospace;font-size:11px;",
		"color:var(--text-muted)}",
		"#zk .zk-badge{display:inline-block;padding:2px 7px;border-radius:9px;",
		"font-size:11px;font-weight:600;white-space:nowrap}",
		"#zk .zk-foot{display:flex;align-items:center;",
		"justify-content:space-between;gap:12px;margin-top:12px;",
		"padding-top:10px;border-top:2px solid var(--border-color)}",
		"#zk .zk-acts{display:flex;gap:8px;align-items:center;flex-wrap:wrap}",
	].join("");
	holder.appendChild(el);
};
