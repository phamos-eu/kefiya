import frappe
from kefiya.utils.import_bank_transaction import ImportBankTransaction

# IBAN length per country (SWIFT IBAN registry), to know where the IBAN ends
IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BR": 29, "CH": 21, "CR": 22, "CY": 28, "CZ": 24,
    "DE": 22, "DK": 18, "DO": 28, "EE": 20, "ES": 24, "FI": 18, "FO": 18,
    "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27, "GT": 28,
    "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IS": 26, "IT": 27, "JO": 30,
    "KW": 30, "KZ": 20, "LB": 28, "LI": 21, "LT": 20, "LU": 20, "LV": 21,
    "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30,
    "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29,
    "RO": 24, "RS": 22, "SA": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27,
    "TN": 24, "TR": 26, "UA": 29, "VG": 24, "XK": 20,
}


def is_valid_iban(iban):
    """Check the IBAN mod-97 checksum."""
    if not iban.isalnum() or not iban[2:4].isdigit():
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def split_iban(text):
    """Split a leading IBAN off text, as mt-940 5.x glued it to the name.

    Returns (iban, rest), or (None, text) when text doesn't start with a
    valid IBAN, for example a plain account number.
    """
    length = IBAN_LENGTHS.get((text or "")[:2])
    if not length or len(text) < length:
        return None, text
    iban = text[:length]
    if not is_valid_iban(iban):
        return None, text
    return iban, text[length:].strip() or None


def execute():
    """Undo mt-940 5.x parsing on Bank Transactions (phamos/kefiya#48).

    mt-940 5.x put the counterparty IBAN in front of bank_party_name and left
    bank_party_iban empty. Split it off again and fill the party from the
    matching Bank Account. Possible duplicates (the same transaction also
    imported under mt-940 4.x) are only listed in the Error Log.
    """
    rows = frappe.db.sql(
        """
        select name, bank_party_name, party
        from `tabBank Transaction`
        where ifnull(bank_party_iban, '') = ''
            and ifnull(bank_party_name, '') != ''
        """,
        as_dict=True,
    )

    fixed_ibans = set()
    for row in rows:
        iban, name = split_iban(row.bank_party_name)
        if not iban:
            continue

        values = {"bank_party_iban": iban, "bank_party_name": name}
        if not row.party:
            party, party_type, account_number = ImportBankTransaction.get_bank_account_data(iban)
            if party:
                values.update(
                    party=party,
                    party_type=party_type,
                    bank_party_account_number=account_number,
                )
        frappe.db.set_value("Bank Transaction", row.name, values, update_modified=False)
        fixed_ibans.add(iban)

    if fixed_ibans:
        log_possible_duplicates(fixed_ibans)


def log_possible_duplicates(ibans):
    """List Bank Transactions that look imported twice; delete nothing."""
    groups = frappe.db.sql(
        """
        select group_concat(name order by creation separator ', ') as names
        from `tabBank Transaction`
        where docstatus < 2 and bank_party_iban in %(ibans)s
        group by bank_account, date, deposit, withdrawal, bank_party_iban, description
        having count(*) > 1
        """,
        {"ibans": tuple(ibans)},
        as_dict=True,
    )
    if groups:
        frappe.log_error(
            title="Kefiya: possible duplicate Bank Transactions (#48)",
            message="Imported once under mt-940 4.x and once under 5.x. "
            "Check each group and cancel the extra ones:\n\n"
            + "\n".join(g.names for g in groups),
        )
