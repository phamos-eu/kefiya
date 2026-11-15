// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

// Loaded first by kefiya_hub.bundle.js, before the views register themselves.
frappe.provide("kefiya.hub");

// Every view registers itself here: { key, label, render($container, hub),
// optional bind($container, hub) once, actions(page, hub) on each show }
kefiya.hub.views = {};
kefiya.hub.register_view = (view) => {
	kefiya.hub.views[view.key] = view;
};

// Status of a Kefiya Login, shown in the Overview and Connect views
kefiya.hub.LOGIN_INDICATOR = {
	ok: { color: "green", label: __("Up to date") },
	warning: { color: "orange", label: __("Needs attention") },
	error: { color: "red", label: __("Connection failed") },
};
