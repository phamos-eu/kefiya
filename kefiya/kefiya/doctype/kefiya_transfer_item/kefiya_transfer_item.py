# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One payment inside a Kefiya Transfer.

Validation lives on the parent (KefiyaTransfer), which checks IBAN checksums
and amounts across all rows and needs the totals anyway. This controller only
has to exist: Frappe imports a module of the same name for every DocType when
syncing schemas, and a missing one aborts `bench migrate` for the whole app.
"""

from frappe.model.document import Document


class KefiyaTransferItem(Document):
	pass
