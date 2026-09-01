# Server Script `fc_cockpit_data`

Ein Patch zum Einsetzen im Browser, kein lauffähiges Modul — die
Abschnitte sind Bruchstücke aus der Mitte einer Funktion. Deshalb
Markdown: eine Datei mit der Endung `.py` wird beim Release-Check
von Frappe Cloud kompiliert und ein eingerückter Ausschnitt ist
dort ein Syntaxfehler.

```python
# ============================================================================
# PATCH 1 von 7  —  Server Script  fc_cockpit_data
# ----------------------------------------------------------------------------
# SUCHEN (steht direkt nach dem Kommentar "... weiss nichts / ueber sich"):
#
#     pay_login = {}
#     for lrow in frappe.get_all('Kefiya Login', filters={'bank_account': ['!=', '']},
#                                fields=['bank_account', 'name'], order_by='modified',
#                                limit_page_length=0):
#         pay_login[lrow['bank_account']] = lrow['name']
#
# ERSETZEN durch:
# ============================================================================

pay_login = {}
kind_by_acc = {}
for lrow in frappe.get_all('Kefiya Login', filters={'bank_account': ['!=', '']},
                           fields=['bank_account', 'name', 'account_kind'],
                           order_by='modified', limit_page_length=0):
    pay_login[lrow['bank_account']] = lrow['name']
    if lrow.get('account_kind'):
        kind_by_acc[lrow['bank_account']] = lrow['account_kind']

# --- In welchen Abschnitt ein Konto gehoert -------------------------------
#
# Die Kontoart kommt von der Bank und steht am Kefiya Login -- aber nur,
# wo sie ueberhaupt gesetzt wurde. Ihr Vorgabewert ist 'Current Account',
# und den liefert kind_of() auch fuer jeden Zugang, an dem nie jemand etwas
# eingestellt hat. Deshalb zaehlt hier nur, was KEIN Zahlungskonto ist:
# das ist eine Aussage. Alles andere entscheidet weiter der Kontotyp am
# Bankkonto, so wie bisher. Siehe bucket_of() unten.
#
# 'depot' wird hier vollstaendig ermittelt, aber unten nicht angezeigt:
# Depots stehen im WAL-Management. Ermittelt wird es trotzdem, damit ein
# Depot nicht stillschweigend unter den Zahlungskonten landet.
KIND_BUCKET = {'Current Account': 'pay', 'Savings': 'pay', 'Credit Card': 'pay',
               'Loan': 'loan', 'Guarantee / Credit Line': 'aval',
               'Cooperative Shares': 'share', 'Securities Account': 'depot'}
# Collar und Call sind wie ein Aval eine eingeraeumte Linie, kein Guthaben.
TYPE_BUCKET = {'Darlehen': 'loan', 'Aval': 'aval', 'Collar': 'aval',
               'Call': 'aval'}

def bucket_of(mem, is_loan_flag):
    """Der Abschnitt eines Kontos. Kefiya nur dort, wo Kefiya etwas SAGT.

    'Current Account' ist der Vorgabewert von account_kind: kind_of() liefert
    ihn auch fuer jeden Zugang, an dem nie jemand etwas eingestellt hat. Von
    einem echten Girokonto ist er nicht zu unterscheiden.

    Diese Regel stand hier zuerst als "Kefiya zuerst, immer". Der Probelauf
    gegen die echten Daten hat sie widerlegt: acht Darlehenskonten der
    Volksbank tragen einen Kefiya-Zugang mit der unveraenderten Vorgabe. Sie
    waeren unter die Zahlungskonten gewandert -- und damit in die
    Liquiditaet. Nur eine Kontoart, die KEIN Zahlungskonto ist, ist eine
    Aussage; alles andere entscheidet weiter die alte Heuristik.
    """
    for r in mem:
        b = KIND_BUCKET.get(kind_by_acc.get(r.get('name')) or '')
        if b and b != 'pay':
            return b
    for r in mem:
        t = TYPE_BUCKET.get(r.get('account_type') or '')
        if t:
            return t
    if is_loan_flag:
        return 'loan'
    return 'pay'


# ============================================================================
# PATCH 2 von 7  —  gleiches Server Script, Abschnitt 'liquidity'
# ----------------------------------------------------------------------------
# SUCHEN:
#
#         # Ein Aval ist kein Guthaben. Er faellt hier heraus, weil sein
#         # Zwillingssatz am Property Loan haengt -- der einzelne Satz aus
#         # Kefiya weiss davon nichts.
#         if gmg['is_loan']:
#             continue
#
# ERSETZEN durch:
# ============================================================================

        # Nur Zahlungskonten sind Liquiditaet. Ein Aval ist eine eingeraeumte
        # Linie und war nie Geld; ein Geschaeftsanteil ist Geld, das es gibt
        # und das man nicht ausgeben kann -- die Mitgliedschaft muesste
        # gekuendigt werden, Jahre im Voraus.
        #
        # Vorher stand hier nur is_loan. Der Aval fiel damit heraus, aber aus
        # dem falschen Grund: weil sein Zwillingssatz am Property Loan haengt,
        # nicht weil er ein Aval ist. Ein Aval ohne Zwilling zaehlte mit, und
        # die Geschaeftsanteile zaehlten immer mit.
        if bucket_of(gmem, gmg['is_loan']) != 'pay':
            continue


# ============================================================================
# PATCH 3 von 7  —  gleiches Server Script, Abschnitt mit der Kontenliste
# ----------------------------------------------------------------------------
# SUCHEN (in der Schleife "for mem in group_accounts(rows):"):
#
#         mg = merge_group(mem)
#         r = mg['primary']
#         is_loan = mg['is_loan']
#         bal = mg['balance']
#         line = mg['credit_line']
#         if is_loan and frappe.utils.flt(bal) == 0:
#             continue
#         items = [] if is_loan else upcoming_for(r['name'], hd)
#         pr = project(bal, items)
#
# ERSETZEN durch:
# ============================================================================

        mg = merge_group(mem)
        r = mg['primary']
        is_loan = mg['is_loan']
        bucket = bucket_of(mem, is_loan)
        # Depots stehen im WAL-Management. Sie hier ein zweites Mal zu zeigen
        # hiesse, dieselbe Zahl an zwei Stellen zu pflegen.
        if bucket == 'depot':
            continue
        bal = mg['balance']
        line = mg['credit_line']
        if is_loan and frappe.utils.flt(bal) == 0:
            continue
        # Eine Zahlungsvorschau hat nur, wovon auch gezahlt wird. Ein Aval und
        # ein Geschaeftsanteil haben keine.
        items = upcoming_for(r['name'], hd) if bucket == 'pay' else []
        pr = project(bal, items)

# ----------------------------------------------------------------------------
# UND im selben Block, im Dictionary accounts.append({...}):
#
# SUCHEN:
#             'property': mg['property'], 'is_loan': is_loan,
#             'last_tx': lastmap.get(r['name']), 'merged': mg['merged'],
#             'available': bal + line, 'forecast': pr['end'], 'low': pr['min'],
#
# ERSETZEN durch:
# ----------------------------------------------------------------------------

            'property': mg['property'], 'is_loan': is_loan, 'bucket': bucket,
            'last_tx': lastmap.get(r['name']), 'merged': mg['merged'],
            # "Verfuegbar" heisst: Geld, mit dem gezahlt werden kann. Ein
            # Geschaeftsanteil ist Guthaben, das nicht verfuegbar ist -- die
            # Mitgliedschaft muesste gekuendigt werden -- und ein Aval war nie
            # Geld. Beide standen hier bisher mit ihrem vollen Betrag.
            'available': (bal + line) if bucket == 'pay' else 0.0,
            'forecast': pr['end'], 'low': pr['min'],
```
