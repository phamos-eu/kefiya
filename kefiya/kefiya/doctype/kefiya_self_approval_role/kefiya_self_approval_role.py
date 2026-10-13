# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A role whose holders may approve a transfer they entered themselves.

One row per role in Kefiya Settings -> "Roles that may approve alone". Meant
for the people who carry the responsibility anyway -- a managing director
releasing a payment they typed does not need a second signature from someone
who reports to them.
"""

from frappe.model.document import Document


class KefiyaSelfApprovalRole(Document):
	pass
