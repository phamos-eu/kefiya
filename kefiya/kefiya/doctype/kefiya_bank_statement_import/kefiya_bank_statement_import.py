import frappe
from frappe.model.document import Document
import csv
import os
from frappe.utils.file_manager import get_file_path
from datetime import datetime
from frappe import _
import chardet
import hashlib
import locale

DKB_VISA = "DKB VISA"
DKB_GIRO = "DKB Girokonto"

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


def detect_format(rawdata):
	"""Return the DKB format of an export with a known header row, else the encoding chardet detects."""
	statement_format, _header_index = find_header(rawdata)
	if statement_format:
		return statement_format
	return chardet.detect(rawdata)['encoding']


def find_header(rawdata):
	"""Return (format, index of the header row) for a known DKB header in the first lines, else (None, None)."""
	try:
		text = rawdata.decode('utf-8-sig')
	except UnicodeDecodeError:
		return None, None
	for index, row in enumerate(csv.reader(text.splitlines()[:10], delimiter=';')):
		cells = [cell.strip() for cell in row]
		for statement_format, header in DKB_HEADERS.items():
			if cells == header:
				return statement_format, index
	return None, None


def parse_german_amount(amount):
	"""Parse '-1.234,56', '1.500' or '12,5' to a float. The dot is always a thousands separator."""
	return float(amount.strip().replace('.', '').replace(',', '.'))


def parse_statement(rawdata):
	"""Return the booked rows of a DKB export as dicts.

	Rows whose status is not 'Gebucht' (e.g. 'Vorgemerkt') are pending and left out.
	"""
	statement_format, header_index = find_header(rawdata)
	header = DKB_HEADERS[statement_format]
	make_row = {DKB_VISA: dkb_visa_row, DKB_GIRO: dkb_giro_row}[statement_format]
	lines = rawdata.decode('utf-8-sig').splitlines()[header_index + 1:]
	rows = []
	occurrences = {}
	for row in csv.DictReader(lines, fieldnames=header, delimiter=';'):
		if not row[header[0]] or row['Status'] != 'Gebucht':
			continue

		# Identical rows (same day, party and amount) get a running number,
		# so they keep apart and still get the same id in an overlapping export.
		key = tuple(row[column] for column in header)
		occurrences[key] = occurrences.get(key, 0) + 1
		transaction = make_row(row)
		transaction['transaction_id'] = hashlib.md5(
			"{0},{1},{2}".format(statement_format, ",".join(key), occurrences[key]).encode()
		).hexdigest()
		rows.append(transaction)
	return rows


def dkb_visa_row(row):
	description = row['Beschreibung'].strip()
	details = [row['Umsatztyp'].strip()]
	foreign_amount = (row['Fremdwährungsbetrag'] or '').replace('\xa0', ' ').strip()
	if foreign_amount:
		details.append(foreign_amount)

	return {
		'date': datetime.strptime(row['Belegdatum'], '%d.%m.%y').date(),
		'amount': parse_german_amount(row['Betrag (€)']),
		'party_name': description,
		'description': "{0} ({1})".format(description, ", ".join(filter(None, details))),
	}


def dkb_giro_row(row):
	amount = parse_german_amount(row['Betrag (€)'])
	# The counterparty is the payee of a withdrawal and the payer of a deposit
	party_name = row['Zahlungsempfänger*in'] if amount < 0 else row['Zahlungspflichtige*r']

	return {
		'date': datetime.strptime(row['Buchungsdatum'], '%d.%m.%y').date(),
		'amount': amount,
		'party_name': party_name.strip(),
		'party_iban': row['IBAN'].strip(),
		'description': row['Verwendungszweck'].strip(),
		'reference_number': row['Kundenreferenz'].strip(),
	}


class KefiyaBankStatementImport(Document):

	@frappe.whitelist()
	def start_import(self, file_url, bank_account, company):
		file_path = self.get_file_from_url(file_url)
		# Detect encoding
		with open(file_path, 'rb') as f:
			rawdata = f.read()

		encoding = detect_format(rawdata)
		if encoding in DKB_HEADERS:
			self.import_dkb(rawdata, bank_account, company)
			return

		total_rows = sum(1 for _ in open(file_path, mode='r', encoding=encoding))-7
		self.db_set('payload_count', total_rows)

		try:
			with open(file_path, mode='r', encoding=encoding, errors='replace') as csvfile:
				csv_reader = csv.reader(csvfile)
                # Skip header (the first 7 rows)
				for _ in range(7):
					next(csv_reader)

				frappe.publish_progress(0, title='Importing Bank Transaction', description='Starting import...')
                
				for index, row in enumerate(csv_reader):
					if encoding == 'utf-8':
						self.create_new_doc_utf8(row, bank_account, company, index, total_rows)
					elif encoding == 'ISO-8859-1':
						self.create_new_doc_iso(row, bank_account, company, index, total_rows)
					else:
						frappe.msgprint("Unsupported file format. Only utf-8 and ISO-8859-1 formats are supported currently.")
						
						self.update_status('Error')
						return

					index += 1
					progress = int((index / total_rows) * 100)
					frappe.publish_progress(progress, title='Importing Bank Transaction', description=f'Processing row {index}/{total_rows}')

		except Exception as e:
			frappe.msgprint(f'Error during import: {e}')
			self.update_status('Error')


	def import_dkb(self, rawdata, bank_account, company):
		"""Create a Bank Transaction per booked row; rows imported before are skipped."""
		rows = parse_statement(rawdata)
		self.db_set('payload_count', len(rows))
		imported, skipped, failed = 0, 0, 0
		frappe.publish_progress(0, title='Importing Bank Transaction', description='Starting import...')

		for index, row in enumerate(rows):
			if frappe.db.exists('Bank Transaction', {
				'transaction_id': row['transaction_id'],
				'bank_account': bank_account,
				'docstatus': ['<', 2],
			}):
				skipped += 1
				continue

			try:
				party, party_type = get_bank_account_data(row['party_iban']) if row.get('party_iban') else ('', '')
				bank_transaction = frappe.new_doc("Bank Transaction")
				bank_transaction.update({
					"date": row['date'],
					"deposit": row['amount'] if row['amount'] > 0 else 0,
					"withdrawal": -row['amount'] if row['amount'] < 0 else 0,
					"bank_account": bank_account,
					"company": company,
					"description": row['description'],
					"bank_party_name": row['party_name'],
					"bank_party_iban": row.get('party_iban'),
					"party": party,
					"party_type": party_type,
					"reference_number": row.get('reference_number'),
					"transaction_id": row['transaction_id'],
				})
				bank_transaction.insert()
				if self.submit_after_success:
					bank_transaction.submit()
				imported += 1
			except Exception as e:
				failed += 1
				frappe.msgprint(f'Error creating document for row: {row} - {e}')

			progress = int(((index + 1) / len(rows)) * 100)
			frappe.publish_progress(progress, title='Importing Bank Transaction', description=f'Processing row {index + 1}/{len(rows)}')

		self.db_set('imported_records', imported)
		if skipped:
			frappe.msgprint(_('{0} transactions were imported before and were skipped.').format(skipped))
		if not failed:
			self.update_status('Success')
		else:
			self.update_status('Partial Success' if imported else 'Error')

	def update_status(self, status):
		self.db_set('status', status)
		frappe.publish_realtime('update_import_status', {'docname': self.name, 'status': status}, user=frappe.session.user)

	def get_file_from_url(self, file_url):
		
		base = frappe.local.site_path
		file_path = base + file_url

		if not os.path.exists(file_path):
			frappe.throw(_('File not found: {0}').format(file_path))
		
		return file_path

	# utf-8
	def create_new_doc_utf8(self, row_data, bank_account, company, index, total_rows):
        # This function create a new document from a csv row
		try:
			bank_transaction = frappe.new_doc("Bank Transaction")
			date =  datetime.strptime( row_data[0], '%d.%m.%Y')
			description = row_data[4]
			bank_party_iban = row_data[5]
			bank_party_name = row_data[3]
			party, party_type = get_bank_account_data(bank_party_iban)
			deposit, withdrawal = self.format_amount_utf8(row_data[7])	

			bank_transaction.update({
				"date": date.strftime('%Y-%m-%d'),
				"deposit": deposit,
				"withdrawal": withdrawal, 
				"bank_account": bank_account,
				"company": company,
				"description": description,
				'bank_party_iban': bank_party_iban,
				'allocated_amount': 0,
				'unallocated_amount': abs(withdrawal - deposit),
				'party': party,
				'party_type': party_type,
				"bank_party_name": bank_party_name
			})

			bank_transaction.insert()
			if self.submit_after_success:
				bank_transaction.submit()
				
			if index+1==total_rows and self.status=='Not Started':
				self.db_set('imported_records', index+1)
				self.update_status('Success')

		except Exception as e:
			frappe.msgprint(f'Error creating document for row: {row_data} - {e}')
			self.db_set('imported_records', index)
			self.update_status('Partial Success')

	def format_amount_utf8(self, amount):
		deposit, withdrawal = 0,0

		if '.' in amount:
			amount = amount.replace('.', '')
		if ',' in amount:
			amount = amount.replace(',', '.')

		amount = float(amount)
		if amount >= 0:
			deposit = amount
		else:
			withdrawal = abs(amount)

		return [deposit, withdrawal]


	# ISO-8859-1
	def create_new_doc_iso(self, row_data, bank_account, company, index, total_rows):
        # This function create a new document from a csv row
		
		row_data = ''.join(row_data)
		row_data = row_data.split(';')
		try:
			bank_transaction = frappe.new_doc("Bank Transaction")
			date =  datetime.strptime( row_data[0], '%d.%m.%Y')
			description = row_data[4].replace('"', '')
			bank_party_iban = row_data[5].replace('"', '')
			bank_party_name = row_data[3].replace('"', '')
			party, party_type = get_bank_account_data(bank_party_iban)
			deposit, withdrawal = self.format_amount_iso(row_data[7])			

			bank_transaction.update({
				"date": date.strftime('%Y-%m-%d'),
				"deposit": deposit,
				"withdrawal": withdrawal, 
				"bank_account": bank_account,
				"company": company,
				"description": description,
				'bank_party_iban': bank_party_iban,
				'allocated_amount': 0,
				'unallocated_amount': abs(withdrawal - deposit),
				'party': party,
				'party_type': party_type,
				"bank_party_name":  bank_party_name
			})

			bank_transaction.insert()
			if self.submit_after_success:
				bank_transaction.submit()

			if index+1==total_rows and self.status=='Not Started':
				self.db_set('imported_records', index+1)
				self.update_status('Success')
		except Exception as e:
			frappe.msgprint(f'Error creating document for row: {row_data} - {e}')
			
			self.db_set('imported_records', index)
			self.update_status('Partial Success')
	
	def format_amount_iso(self, amount):
		deposit, withdrawal = 0,0

		if '"' in amount:
			amount = amount.replace('"', '')

		if ',' in amount and '.' in amount:
			amount = amount.replace('.', '').replace(',', '.')
		elif ',' in amount:
			amount = amount.replace(',', '.')
		elif '.' in amount:
			amount = amount.replace('.', '')
			integer_part = amount[:-2]
			decimal_part = amount[-2:]
			amount = integer_part + '.' + decimal_part
		else:
			try:
				integer_part = amount[:-2]
				decimal_part = amount[-2:]
				amount = integer_part + '.' + decimal_part
			except Exception as e:
				frappe.msgprint(f'currency formatting not supported for row: {row_data} - {e}')

		amount = float(amount)
		if amount >= 0:
			deposit = amount
		else:
			withdrawal = abs(amount)

		return [deposit, withdrawal]
	
def get_bank_account_data(IBAN):
	party, party_type = '', ''
	bank_account_exists = frappe.db.exists('Bank Account', {'iban': IBAN})
	
	if bank_account_exists:
		bank_account_doc = frappe.get_doc('Bank Account', {'iban': IBAN})
		party = bank_account_doc.party
		party_type = bank_account_doc.party_type

	return [party, party_type]