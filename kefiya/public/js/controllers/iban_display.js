// Copyright (c) 2026, Phamos GmbH and contributors
// For license information, please see license.txt

// An IBAN as people read it, and an IBAN as machines take it.
//
// DE27672500200009355367 is twenty-two characters with no landmarks. Nobody
// checks that against a letter from the bank; the eye slides off it, and a
// transposed digit pays a stranger. Printed in fours it becomes checkable:
//
//     DE27 6725 0020 0009 3553 67
//
// That is how banks print it, how the letter shows it, and how the person
// comparing the two needs it. It is a display rule only -- what gets STORED
// and what goes into a pain.001 stays unbroken, because a space inside an
// IBAN is a rejected order.

frappe.provide("kefiya");

kefiya.iban_plain = function (value) {
	return String(value || "").replace(/[\s-]/g, "").toUpperCase();
};

kefiya.iban_pretty = function (value) {
	return kefiya.iban_plain(value).replace(/(.{4})/g, "$1 ").trim();
};
