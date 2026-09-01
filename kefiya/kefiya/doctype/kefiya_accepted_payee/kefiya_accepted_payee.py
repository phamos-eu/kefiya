# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""A payee a person looked at once, so nobody is asked to look again.

Written only by the approval path, and read only by the send path. Nobody
creates one by hand, which is why no role has create or write on it: a row
here means somebody approved a payment, and a row that appeared any other way
would say something untrue about a person.

Deleting one is allowed, and is the way to withdraw a decision -- the next
order to that payee stops and waits again.
"""

from frappe.model.document import Document


class KefiyaAcceptedPayee(Document):
    pass
