# -*- coding: utf-8 -*-
# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""One business transaction the bank named for one account.

The rows are written by kefiya.utils.account_capabilities from what the bank
sends at logon; nobody types them in. Validation lives there, because the
question "is this list still current?" is about the whole list and not about a
single row. This controller only has to exist: Frappe imports a module of the
same name for every DocType when syncing schemas, and a missing one aborts
`bench migrate` for the whole app.
"""

from frappe.model.document import Document


class KefiyaAccountCapability(Document):
	pass
