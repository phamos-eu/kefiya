// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

// THIS FILE IS NOT LOADED BY THE APP.
//
// It is the script of the Custom HTML Block "axessio_zahlungsausgang", kept
// here so it is versioned like everything else. The block's HTML field stays
// what it always was:
//
//     <div id='zk'></div>
//
// and its style field is now empty -- the styling travels with
// controllers/payment_outbox.js.
//
// Everything the page does lives in the app. That is the whole point: what
// used to be 19 KB of JavaScript inside a database row, on the page that sends
// money, is now reviewed, tested and deployed like code.
//
// The guard below matters more than it looks. The block is stored on the site
// and the app is deployed separately, so between a block that has been updated
// and an app that has not there is a window in which kefiya.payment_outbox
// does not exist yet. Without the guard that window is a blank page on the
// outgoing payments; with it, it is a sentence and a way on.

var sr = (typeof root_element !== "undefined" && root_element)
	? root_element : document;
var root = sr.querySelector("#zk");

if (root) {
	if (window.kefiya && kefiya.payment_outbox) {
		kefiya.payment_outbox(root);
	} else {
		root.innerHTML =
			"<div style='padding:16px;background:#FDF3E3;color:#854F0B;"
			+ "border-radius:6px'>"
			+ "<div><b>Die Seite braucht ein Update der App.</b></div>"
			+ "<div style='margin-top:6px'>Der Zahlungsausgang wird jetzt von"
			+ " kefiya geliefert. Sobald die App aktualisiert ist, erscheint er"
			+ " hier wieder.</div>"
			+ "<div style='margin-top:10px'>"
			+ "<a class='btn btn-default btn-sm'"
			+ " href='/app/kefiya-transfer'>Zur Auftragsliste</a> "
			+ "<button class='btn btn-default btn-sm'"
			+ " onclick='location.reload()'>Neu laden</button>"
			+ "</div></div>";
	}
}
