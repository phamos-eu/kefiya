import frappe
from frappe.model.document import Document
import csv
import io
import os
from frappe.utils import get_files_path
from datetime import datetime
from frappe import _
import chardet
import hashlib
import json

from frappe.utils import cint, flt, strip_html
from frappe.utils.background_jobs import is_job_enqueued
from frappe.utils.synchronization import filelock

from kefiya.utils.statement_import import importer, reader
from kefiya.utils.statement_import import mapping as statement_mapping
from kefiya.utils.statement_import import templates as statement_templates
from kefiya.utils.statement_import.guess import guess_mapping
from kefiya.utils.statement_import.presets import (  # noqa: F401 - imported from here by tests
	DKB_GIRO,
	DKB_HEADERS,
	DKB_VISA,
	PAYPAL,
	PAYPAL_COLUMNS,
	PRESETS,
)
from kefiya.utils.statement_import.transform import parse_german_amount, transform

PREVIEW_SAMPLE_ROWS = 15
PREVIEW_MAX_ROWS = 500
IMPORT_LOG_LIMIT = 2000
IMPORT_TIMEOUT = 60 * 60
MAX_ROW_ACCOUNTS = 5000
PROGRESS_EVERY = 10


def detect_format(rawdata):
	"""Return the format of an export with a known header row, else the encoding chardet detects."""
	statement_format, _header_index = find_header(rawdata)
	if statement_format:
		return statement_format
	return chardet.detect(rawdata)['encoding']


def find_header(rawdata):
	"""Return (format, index of the header row) for a known header in the first lines, else (None, None)."""
	try:
		text = rawdata.decode('utf-8-sig')
	except UnicodeDecodeError:
		return None, None
	lines = text.splitlines()[:10]
	for index, row in enumerate(csv.reader(lines, delimiter=';')):
		cells = [cell.strip() for cell in row]
		for statement_format, header in DKB_HEADERS.items():
			if cells == header:
				return statement_format, index
	# A PayPal export starts with its header row and is separated by commas
	paypal_header = next(csv.reader(lines[:1]), [])
	if set(PAYPAL_COLUMNS) <= {cell.strip() for cell in paypal_header}:
		return PAYPAL, 0
	return None, None


def parse_statement(rawdata, errors=None):
	"""Return the transactions of a DKB or PayPal export as dicts, by the built-in mapping.

	DKB rows whose status is not 'Gebucht' (e.g. 'Vorgemerkt') are pending and left out,
	as are PayPal's 'Memo' rows. A row that can't be read (bad date or amount, missing
	columns) raises ValueError, unless an `errors` list is given: then the row is skipped
	and (file line, message) is appended.
	"""
	statement_format, header_index = find_header(rawdata)
	preset = statement_mapping.validate(PRESETS[statement_format])
	table = reader.read(rawdata, delimiter=preset['delimiter'], header_row=header_index)
	result = transform(table, preset)
	if result['errors']:
		if errors is None:
			raise ValueError(result['errors'][0][1])
		errors.extend(result['errors'])
	return [transaction for _line, transaction in result['transactions']]


class KefiyaBankStatementImport(Document):

	def read_file(self) -> bytes:
		# get_file_from_url returns only the path of a File the user may read, and only
		# when it lies inside the site's files folders
		with open(self.get_file_from_url(self.import_file), 'rb') as f:  # nosemgrep
			return f.read()

	@frappe.whitelist()
	def get_preview(self, mapping=None, template=None, import_as=None):
		"""Show what an import with this mapping would do; nothing is written.

		Without a mapping, the given template is used, else the one that fits the file,
		else a mapping guessed from the column names.
		"""
		self.check_permission('read')
		rawdata = self.read_file()
		candidates = statement_templates.get_candidates()
		template_key = None
		if mapping:
			clean = statement_mapping.validate(mapping)
			template_key = template
		elif template:
			candidate = next((c for c in candidates if c['key'] == template), None)
			if not candidate:
				frappe.throw(_('Template {0} not found.').format(template))
			fitting = statement_templates.suggest(rawdata, [candidate])
			clean = (fitting or candidate)['mapping']
			template_key = template
		else:
			fitting = statement_templates.suggest(rawdata, candidates)
			if fitting:
				clean, template_key = fitting['mapping'], fitting['key']
			else:
				clean = guess_mapping(reader.read(rawdata))

		table = reader.read(rawdata, clean.get('delimiter'), clean.get('header_row'))
		clean = dict(clean, delimiter=table['delimiter'], header_row=table['header_row'])
		preview = {
			'file': {
				'encoding': table['encoding'],
				'delimiter': table['delimiter'],
				'header_row': table['header_row'],
				'header': table['header'],
				'sample': [{'line': line, 'cells': row} for line, row in table['rows'][:PREVIEW_SAMPLE_ROWS]],
				'row_count': len(table['rows']),
			},
			'templates': [{'key': c['key'], 'label': c['label'], 'builtin': c['builtin']} for c in candidates],
			'template': template_key,
			'mapping': clean,
			'mapping_error': None,
			'rows': [],
			'totals': {},
			'options': {
				'targets': statement_mapping.COLUMN_TARGETS,
				'date_formats': statement_mapping.DATE_FORMATS,
				'number_formats': statement_mapping.NUMBER_FORMATS,
			},
		}
		try:
			clean = statement_mapping.validate(clean, table['header'])
		except statement_mapping.MappingError as e:
			preview['mapping_error'] = str(e)
			return preview

		result = transform(table, clean)
		entries = importer.classify(
			result['transactions'], result['errors'], self.bank_account, import_as or self.import_as or importer.BANK_TRANSACTION
		)
		preview['rows'] = [preview_row(entry) for entry in entries[:PREVIEW_MAX_ROWS]]
		preview['totals'] = preview_totals(entries, result['skipped'])
		preview['totals']['other_transactions_in_period'] = other_transactions_in_period(self.bank_account, entries)
		return preview

	@frappe.whitelist(methods=["POST"])
	def start_import(self, file_url=None, bank_account=None, company=None, mapping=None, template=None,
					 import_as=None, counter_account=None, fee_account=None, row_accounts=None):
		"""Import this doc's file into this doc's bank account.

		The first three arguments come from the client and are only checked against the doc,
		so a caller cannot import another file or into another account. Without a mapping,
		a DKB or PayPal export is imported by its built-in mapping, other files by the old
		fixed-position import. With a mapping the import runs in the background.
		"""
		self.check_permission('write')
		for value, fieldname in ((file_url, 'import_file'), (bank_account, 'bank_account'), (company, 'company')):
			if value and value != self.get(fieldname):
				frappe.throw(_('{0} does not match this import.').format(_(self.meta.get_label(fieldname))))
		if frappe.db.get_value('Bank Account', self.bank_account, 'company') != self.company:
			frappe.throw(_('Bank Account {0} does not belong to company {1}.').format(self.bank_account, self.company))
		if self.status in ('Queued', 'In Progress') and is_job_enqueued(self.job_id()):
			frappe.throw(_('This import is running already.'))

		rawdata = self.read_file()
		if not mapping and not template:
			statement_format, header_index = find_header(rawdata)
			if not statement_format:
				self.start_legacy_import(rawdata)
				return
			mapping = dict(PRESETS[statement_format], header_row=header_index)
			template = statement_templates.BUILTIN_PREFIX + statement_format
		elif not mapping:
			candidate = statement_templates.get_candidate(template)
			if not candidate:
				frappe.throw(_('Template {0} not found.').format(template))
			mapping = (statement_templates.suggest(rawdata, [candidate]) or candidate)['mapping']

		settings = self.check_import(rawdata, mapping, import_as, counter_account, fee_account, row_accounts)
		settings.update({
			'template': template,
			'file_hash': hashlib.sha256(rawdata).hexdigest(),
			'status': 'Queued',
			'payload_count': 0,
			'imported_records': 0,
			'skipped_records': 0,
			'failed_records': 0,
			'import_log': None,
		})
		self.db_set(settings)
		self.publish_status()
		frappe.enqueue(
			run_import,
			queue='long',
			timeout=IMPORT_TIMEOUT,
			job_id=self.job_id(),
			deduplicate=not frappe.flags.in_test,
			enqueue_after_commit=not frappe.flags.in_test,
			now=bool(frappe.flags.in_test),
			docname=self.name,
		)

	def job_id(self):
		return f'kefiya_statement_import::{self.name}'

	def check_import(self, rawdata, mapping, import_as, counter_account, fee_account, row_accounts) -> dict:
		"""Throw unless the mapping fits the file and the target can be booked; return the settings to keep."""
		try:
			clean = statement_mapping.validate(mapping)
			table = reader.read(rawdata, clean.get('delimiter'), clean.get('header_row'))
			clean = statement_mapping.validate(
				dict(clean, delimiter=table['delimiter'], header_row=table['header_row']), table['header'])
		except statement_mapping.MappingError as e:
			frappe.throw(str(e), title=_('Invalid column mapping'))

		import_as = import_as or self.import_as or importer.BANK_TRANSACTION
		if import_as not in importer.TARGETS:
			frappe.throw(_('Unknown import target: {0}').format(import_as))
		row_accounts = load_row_accounts(row_accounts)

		if import_as == importer.JOURNAL_ENTRY:
			bank = importer.bank_account_details(self.bank_account, self.company)
			importer.check_journal_entry_setup(
				self.company, bank, counter_account, fee_account, row_accounts, cint(self.submit_after_success))
			if row_accounts:
				ids = {transaction['transaction_id'] for _line, transaction in transform(table, clean)['transactions']}
				unknown = [transaction_id for transaction_id in row_accounts if transaction_id not in ids]
				if unknown:
					frappe.throw(_('These rows are not in the file: {0}').format(', '.join(unknown[:5])))
		else:
			counter_account = fee_account = None
			row_accounts = {}

		return {
			'mapping': json.dumps(clean, ensure_ascii=False),
			'import_as': import_as,
			'counter_account': counter_account,
			'fee_account': fee_account,
			'row_accounts': json.dumps(row_accounts) if row_accounts else None,
		}

	def run(self):
		"""Import the file by the mapping kept on the doc; runs as a background job."""
		self.db_set('status', 'In Progress')
		self.publish_status()
		entries = []
		try:
			rawdata = self.read_file()
			if hashlib.sha256(rawdata).hexdigest() != self.file_hash:
				frappe.throw(_('The file was changed after the import was started.'))
			# Checked again: the doc may have been changed since the import was started
			self.check_import(rawdata, self.mapping, self.import_as, self.counter_account, self.fee_account,
							  self.row_accounts)
			clean = statement_mapping.validate(self.mapping)
			table = reader.read(rawdata, clean.get('delimiter'), clean.get('header_row'))
			result = transform(table, clean)

			# One import per bank account at a time, so two of them cannot both find a row new
			with filelock(f'kefiya_statement_import_{frappe.scrub(self.bank_account)}', timeout=IMPORT_TIMEOUT):
				entries = importer.classify(result['transactions'], result['errors'], self.bank_account, self.import_as)
				self.db_set('payload_count', len(result['transactions']) + len(result['errors']))
				self.import_entries(entries, load_row_accounts(self.row_accounts))
		except Exception as e:
			if not frappe.flags.in_test:
				frappe.db.rollback()
			self.finish(entries, [{'line': None, 'status': importer.ERROR, 'message': strip_html(str(e))}])
			if not isinstance(e, frappe.ValidationError):
				frappe.log_error(title=_('Bank statement import {0} failed').format(self.name))
			return
		self.finish(entries, [])

	def import_entries(self, entries, row_accounts):
		journal_entries = self.import_as == importer.JOURNAL_ENTRY
		bank = importer.bank_account_details(self.bank_account, self.company)
		submit = cint(self.submit_after_success)
		total = len(entries)
		for index, entry in enumerate(entries):
			if entry['status'] == importer.NEW:
				transaction = entry['transaction']
				savepoint = 'kefiya_statement_row'
				frappe.db.savepoint(savepoint)
				try:
					if journal_entries:
						account = importer.counter_account_for(
							transaction, self.counter_account, self.fee_account, row_accounts)
						name = importer.make_journal_entry(transaction, bank, self.company, account, submit)
					else:
						name = importer.make_bank_transaction(transaction, self.bank_account, self.company, submit)
					entry.update(status=importer.IMPORTED, doctype=self.import_as, name=name)
				except Exception as e:
					# A failed submit must not leave a half-written document behind
					frappe.db.rollback(save_point=savepoint)
					entry.update(status=importer.ERROR, message=strip_html(str(e)) or type(e).__name__)
				# Each row is its own document: keep the rows done so far, even if the
				# background job dies later, and let the progress shown in the form be true
				if not frappe.flags.in_test:
					frappe.db.commit()  # nosemgrep
			if (index + 1) % PROGRESS_EVERY == 0 or index + 1 == total:
				frappe.publish_realtime(
					'kefiya_statement_import_progress',
					{'docname': self.name, 'current': index + 1, 'total': total},
					user=frappe.session.user,
				)

	def finish(self, entries, extra_log):
		def count(status):
			return sum(1 for entry in entries if entry['status'] == status)

		imported, failed = count(importer.IMPORTED), count(importer.ERROR) + len(extra_log)
		# Problems first, so they are within the limit
		log = extra_log + [import_log_row(entry) for entry in entries if entry['status'] != importer.IMPORTED]
		log += [import_log_row(entry) for entry in entries if entry['status'] == importer.IMPORTED]
		self.db_set({
			'imported_records': imported,
			'skipped_records': count(importer.DUPLICATE) + count(importer.SKIPPED),
			'failed_records': failed,
			'import_log': json.dumps(log[:IMPORT_LOG_LIMIT], ensure_ascii=False, default=str),
			'status': 'Success' if not failed else ('Partial Success' if imported else 'Error'),
		})
		self.publish_status()

	def publish_status(self):
		frappe.publish_realtime(
			'update_import_status', {'docname': self.name, 'status': self.status},
			user=frappe.session.user, after_commit=True,
		)

	def update_status(self, status):
		self.db_set('status', status)
		self.publish_status()

	def start_legacy_import(self, rawdata):
		"""Files Kefiya has no mapping for: the old import by fixed column positions."""
		encoding = detect_format(rawdata)
		bank_account, company = self.bank_account, self.company
		# The file is read once (read_file); newline=None reads it as open() did
		text = rawdata.decode(encoding or 'utf-8', errors='replace')
		total_rows = sum(1 for _ in io.StringIO(text, newline=None))-7
		self.db_set('payload_count', total_rows)

		try:
			with io.StringIO(text, newline=None) as csvfile:
				csv_reader = csv.reader(csvfile)
				# Skip header (the first 7 rows)
				for _row in range(7):
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

	def get_file_from_url(self, file_url):
		"""Return the path of the uploaded File with this url; it must lie in the site's files folders."""
		file_name = frappe.db.get_value('File', {'file_url': file_url}, 'name') if file_url else None
		if not file_name:
			frappe.throw(_('File not found: {0}').format(file_url))
		file_doc = frappe.get_doc('File', file_name)
		file_doc.check_permission('read')

		file_path = os.path.realpath(file_doc.get_full_path())
		files_folders = [os.path.realpath(get_files_path(is_private=is_private)) for is_private in (0, 1)]
		if not any(file_path.startswith(folder + os.sep) for folder in files_folders):
			frappe.throw(_('File not found: {0}').format(file_url))
		if not os.path.exists(file_path):
			frappe.throw(_('File not found: {0}').format(file_url))

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
		"""Split a German amount such as '"-1.234,56"' into [deposit, withdrawal]."""
		deposit, withdrawal = 0, 0

		amount = parse_german_amount(amount.replace('"', ''))
		if amount >= 0:
			deposit = amount
		else:
			withdrawal = abs(amount)

		return [deposit, withdrawal]

def run_import(docname):
	"""Background job of start_import."""
	frappe.get_doc('Kefiya Bank Statement Import', docname).run()


def load_row_accounts(row_accounts) -> dict:
	"""Return {transaction id: account} from the browser or the doc, checked for its shape."""
	if not row_accounts:
		return {}
	if isinstance(row_accounts, str):
		try:
			row_accounts = json.loads(row_accounts)
		except ValueError:
			frappe.throw(_('The row accounts are not valid JSON.'))
	if not isinstance(row_accounts, dict) or len(row_accounts) > MAX_ROW_ACCOUNTS or not all(
		isinstance(key, str) and isinstance(value, str) and value for key, value in row_accounts.items()
	):
		frappe.throw(_('The row accounts must map transaction ids to accounts.'))
	return row_accounts


def preview_row(entry) -> dict:
	row = {'line': entry['line'], 'status': entry['status'], 'message': entry['message']}
	transaction = entry['transaction']
	if transaction:
		row.update({key: transaction.get(key) for key in (
			'date', 'amount', 'currency', 'party_name', 'description', 'reference_number', 'party_iban',
			'transaction_id',
		)})
	return row


def preview_totals(entries, skipped_rows) -> dict:
	totals = {status: 0 for status in (importer.NEW, importer.DUPLICATE, importer.ERROR, importer.SKIPPED)}
	deposits = withdrawals = 0
	dates = []
	for entry in entries:
		totals[entry['status']] += 1
		transaction = entry['transaction']
		if transaction:
			dates.append(transaction['date'])
		if entry['status'] == importer.NEW:
			if transaction['amount'] > 0:
				deposits += transaction['amount']
			else:
				withdrawals -= transaction['amount']
	totals.update({
		'rows': len(entries),
		'filtered_rows': skipped_rows,
		'deposits': flt(deposits, 2),
		'withdrawals': flt(withdrawals, 2),
		'from_date': min(dates) if dates else None,
		'to_date': max(dates) if dates else None,
	})
	return totals


def other_transactions_in_period(bank_account, entries) -> int:
	"""Count Bank Transactions of the account in the file's period that no row of the file matches.

	FinTS imports and other files give rows other ids, so a row booked that way is not
	recognised as a duplicate; this count is a hint to look closer.
	"""
	transactions = [entry['transaction'] for entry in entries if entry['transaction']]
	if not transactions:
		return 0
	ids = {transaction['transaction_id'] for transaction in transactions}
	existing = frappe.get_all(
		'Bank Transaction',
		filters={
			'bank_account': bank_account,
			'docstatus': ['<', 2],
			'date': ['between', [min(t['date'] for t in transactions), max(t['date'] for t in transactions)]],
		},
		pluck='transaction_id',
	)
	return sum(1 for transaction_id in existing if transaction_id not in ids)


def import_log_row(entry) -> dict:
	"""One line of the import log: no file contents beyond the date, amount and id."""
	transaction = entry.get('transaction') or {}
	return {
		'line': entry.get('line'),
		'status': entry['status'],
		'message': entry.get('message') or '',
		'transaction_id': transaction.get('transaction_id'),
		'date': transaction.get('date'),
		'amount': transaction.get('amount'),
		'doctype': entry.get('doctype'),
		'name': entry.get('name'),
	}


@frappe.whitelist()
def save_import_template(template_name, mapping, header_columns=None):
	"""Save a column mapping as a template, or update the template of this name."""
	if isinstance(header_columns, str):
		header_columns = json.loads(header_columns)
	values = {
		'mapping': json.dumps(statement_mapping.load(mapping), ensure_ascii=False),
		'header_columns': '\n'.join(str(column) for column in (header_columns or [])[:200]),
	}
	if frappe.db.exists('Kefiya Statement Import Template', template_name):
		doc = frappe.get_doc('Kefiya Statement Import Template', template_name)
		doc.update(values)
		doc.save()
	else:
		doc = frappe.get_doc(dict(values, doctype='Kefiya Statement Import Template', template_name=template_name))
		doc.insert()
	return doc.name



def get_bank_account_data(IBAN):
	party, party_type = '', ''
	bank_account_exists = frappe.db.exists('Bank Account', {'iban': IBAN})
	
	if bank_account_exists:
		bank_account_doc = frappe.get_doc('Bank Account', {'iban': IBAN})
		party = bank_account_doc.party
		party_type = bank_account_doc.party_type

	return [party, party_type]