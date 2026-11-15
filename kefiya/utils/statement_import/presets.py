# Copyright (c) 2026, Phamos GmbH and contributors
# For license information, please see license.txt

"""Built-in mappings for exports Kefiya knows.

They give the same transactions as the hardcoded parsers they replaced, down to the
transaction ids, so files imported before are recognised again on a re-import.
"""

DKB_VISA = "DKB VISA"
DKB_GIRO = "DKB Girokonto"
PAYPAL = "PayPal"

# Header rows of the DKB "Umsatzliste" exports, by format
DKB_HEADERS = {
    DKB_VISA: [
        "Belegdatum", "Wertstellung", "Status", "Beschreibung",
        "Umsatztyp", "Betrag (€)", "Fremdwährungsbetrag",
    ],
    DKB_GIRO: [
        "Buchungsdatum", "Wertstellung", "Status", "Zahlungspflichtige*r",
        "Zahlungsempfänger*in", "Verwendungszweck", "Umsatztyp", "IBAN",
        "Betrag (€)", "Gläubiger-ID", "Mandatsreferenz", "Kundenreferenz",
    ],
}

# Columns the import needs from a German PayPal activity export ("Aktivitäten herunterladen").
# Which other columns an export has depends on the download settings. The buyer's e-mail,
# address and phone number are in the file too, but are not needed and not taken over.
PAYPAL_COLUMNS = [
    "Datum", "Name", "Typ", "Währung", "Brutto", "Gebühr",
    "Transaktionscode", "Auswirkung auf Guthaben",
]

# Pending rows ("Vorgemerkt") are left out; they come again once booked
DKB_BOOKED_ONLY = [{"column": "Status", "op": "not_equals", "value": "Gebucht"}]

PRESETS = {
    DKB_VISA: {
        "delimiter": ";",
        "number_format": "de",
        "date_format": "%d.%m.%y",
        "columns": {
            "date": "Belegdatum",
            "amount": "Betrag (€)",
            "party_name": "Beschreibung",
        },
        "description": {
            "columns": ["Beschreibung", "Umsatztyp", "Fremdwährungsbetrag"],
            "formatter": "dkb_visa",
        },
        "filters": DKB_BOOKED_ONLY,
        "id": {"seed": DKB_VISA, "columns": DKB_HEADERS[DKB_VISA]},
    },
    DKB_GIRO: {
        "delimiter": ";",
        "number_format": "de",
        "date_format": "%d.%m.%y",
        "columns": {
            "date": "Buchungsdatum",
            "amount": "Betrag (€)",
            "party_name_deposit": "Zahlungspflichtige*r",
            "party_name_withdrawal": "Zahlungsempfänger*in",
            "party_iban": "IBAN",
            "reference_number": "Kundenreferenz",
        },
        "description": {"columns": ["Verwendungszweck"], "formatter": "joined"},
        "filters": DKB_BOOKED_ONLY,
        "id": {"seed": DKB_GIRO, "columns": DKB_HEADERS[DKB_GIRO]},
    },
    PAYPAL: {
        "delimiter": ",",
        "number_format": "de",
        "date_format": "%d.%m.%Y",
        "columns": {
            "date": "Datum",
            "amount": "Brutto",
            "currency": "Währung",
            "party_name": "Name",
            "reference_number": "Rechnungsnummer",
            # PayPal's own code, so a row keeps its id when its status changes in a later export
            "transaction_id": "Transaktionscode",
        },
        "description": {
            "columns": ["Typ", "Betreff", "Zugehöriger Transaktionscode"],
            "formatter": "paypal",
        },
        # Kept apart from the gross amount, so that a payment matches its invoice in full;
        # together they are PayPal's net amount, so the balance stays the one PayPal shows.
        "fee": {
            "column": "Gebühr",
            "party_name": "PayPal",
            "description": "PayPal Gebühr zu {id}",
            "reference": "{id}",
        },
        # 'Memo' rows (authorisations) move no money. Pending rows count: PayPal has already
        # taken a hold off the balance and books it back in a row of its own.
        "filters": [{"column": "Auswirkung auf Guthaben", "op": "equals", "value": "Memo"}],
        "optional_columns": ["Rechnungsnummer", "Betreff", "Zugehöriger Transaktionscode"],
    },
}

# Exact header rows of the built-ins; a PayPal export may have more columns
PRESET_HEADERS = {DKB_VISA: DKB_HEADERS[DKB_VISA], DKB_GIRO: DKB_HEADERS[DKB_GIRO], PAYPAL: PAYPAL_COLUMNS}
