// Copyright (c) 2019, jHetzer and contributors
// For license information, please see license.txt

frappe.provide("kefiya.iban_tools");

kefiya.iban_tools = {
	// https://stackoverflow.com/questions/21928083/iban-validation-check
	isValidIBANNumber: function(input) {
		var CODE_LENGTHS = {
			AD: 24, AE: 23, AT: 20, AZ: 28, BA: 20, BE: 16, BG: 22, BH: 22, BR: 29,
			CH: 21, CR: 21, CY: 28, CZ: 24, DE: 22, DK: 18, DO: 28, EE: 20, ES: 24,
			FI: 18, FO: 18, FR: 27, GB: 22, GI: 23, GL: 18, GR: 27, GT: 28, HR: 21,
			HU: 28, IE: 22, IL: 23, IS: 26, IT: 27, JO: 30, KW: 30, KZ: 20, LB: 28,
			LI: 21, LT: 20, LU: 20, LV: 21, MC: 27, MD: 24, ME: 22, MK: 19, MR: 27,
			MT: 31, MU: 30, NL: 18, NO: 15, PK: 24, PL: 28, PS: 29, PT: 25, QA: 29,
			RO: 24, RS: 22, SA: 24, SE: 24, SI: 19, SK: 24, SM: 27, TN: 24, TR: 26
		};
		// var iban = String(input).toUpperCase().replace(/[^A-Z0-9]/g, ''), // keep only alphanumeric characters
		var iban = String(input).toUpperCase(), // keep only alphanumeric characters
			code = iban.match(/^([A-Z]{2})(\\\\d{2})([A-Z\\\\d]+)$/), // match and capture (1) the country code, (2) the check digits, and (3) the rest
			digits;
		// check syntax and length
		if (!code || iban.length !== CODE_LENGTHS[code[1]]) {
			return false;
		}
		// rearrange country code and check digits, and convert chars to ints
		digits = (code[3] + code[1] + code[2]).replace(/[A-Z]/g, function (letter) {
			return letter.charCodeAt(0) - 55;
		});
		// final check
		return kefiya.iban_tools.mod97(digits);
	},
	mod97: function(string) {
		var checksum = string.slice(0, 2), fragment;
		for (var offset = 2; offset < string.length; offset += 7) {
			fragment = String(checksum) + string.substring(offset, offset + 7);
			checksum = parseInt(fragment, 10) % 97;
		}
		return checksum;
	},
	// https://github.com/jquery-validation/jquery-validation/blob/master/src/additional/bic.js
	isValidBic: function(value) {
		return /^([A-Z]{6}[A-Z2-9][A-NP-Z1-9])(X{3}|[A-WY-Z0-9][A-Z0-9]{2})?$/.test( value.toUpperCase() );
	},
	getBankDetailsByIBAN: function(iban,callback) {
		const regex = /(?<=.{12})./gi;
		// https://openiban.com/#additional-info
		const supported_countries = [
			"BE", // Belgium
			"DE", // Germany
			"NL", // Netherlands
			"LU", // Luxembourg
			"CH", // Switzerland
			"AT", // Austria
			"LI", // Liechtenstein
		];
		
		var ibanCountryCode = iban.substring(0, 2).toUpperCase();
		if(supported_countries.indexOf(ibanCountryCode) > -1){
			var url = "https://openiban.com/validate/" +
				iban.replace(regex, "0") +
				"?getBIC=true&validateBankCode=true";
			$.ajax({
				url: url,
				type: 'GET',
				success: function(data){
					
					if(data.checkResults.bankCode){
						callback(data);
					}else{
						frappe.throw(__("Could not fetch bank details"));
					}
				},
				error: function(/* data */) {
					frappe.throw(__("Could not fetch bank details"));
				}
			});
		}else{
			data = {
				valid: false,
				messages: 'Invalid IBAN',
				iban: iban,
				bankData: {bankCode: '', name: '', zip: '', city: '', bic: ''},
				checkResults: {bankCode: true}
			}
			
			callback(data)
			// frappe.throw(__("Unsupported IBAN country code: {0}",["<b>"+ibanCountryCode+"</b>"]));
		}
	},
	getPartyName: function(party_type, party) {
		return new Promise((resolve, reject) => {
			frappe.call({
				method: 'frappe.client.get',
				args: {
					doctype: party_type,
					name: party
				},
				callback: function(r) {
					if (r.message) {
						if (party_type === "Customer"){
							resolve(r.message.customer_name);
						}
						else if (party_type === "Supplier"){
							resolve(r.message.supplier_name);
						}
					} else {
						reject("No party found");
					}
				},
				error: function(err) {
					reject(err);
				}
			});
		});
	},	
	createPartyBankAccount: function(frm, bankInfo, resultCallback) {
		frappe.call({
			method: "kefiya.utils.client.new_bank_account",
			args: {
				payment_doc: frm,
				bankData: bankInfo.bankData,
			},
			callback: resultCallback,
			/*
			callback: function(r) {
				console.log(r);
			},*/
		});
	},
	setPartyBankAccount: function(frm, callback) {
		kefiya.iban_tools.getBankDetailsByIBAN(frm.doc.bank_party_iban
			, async function(data) {
			
			if (data.checkResults.bankCode) {
				
				let defalutValue = frm.doc.party_type ? frm.doc.party_type:frm.doc.deposit > 0 ? 'Customer': 'Supplier'

				let party_name = ""
				if (defalutValue && frm.doc.party){
					party_name = await kefiya.iban_tools.getPartyName(defalutValue, frm.doc.party)
				}
				
				let dialog = new frappe.ui.Dialog({
					title: __('Create Bank Account'),
					fields: [{
						label: 'Bank Account',
						fieldtype: 'Section Break',
					}, {
						label: 'IBAN',
						fieldname: 'iban',
						fieldtype: 'Data',
						read_only: 1,
						reqd: 1,
						default: frm.doc.bank_party_iban,
					}, {
						label: 'Date',
						fieldname: 'date',
						fieldtype: 'Date',
						default: frm.doc.date,
					}, {
						label: 'Depost',
						fieldname: 'deposit',
						fieldtype: 'Currency',
						read_only: 1,
						default: frm.doc.deposit,
					},  {
						label: 'Withdrawal',
						fieldname: 'withdrawal',
						fieldtype: 'Currency',
						read_only: 1,
						default: frm.doc.withdrawal,
					},  {
						fieldname: 'col_break1',
						fieldtype: 'Column Break',
					}, {
						label: 'Sender',
						fieldname: 'sender',
						fieldtype: 'Data',
						// read_only: 1,
						reqd: 1,
						default: frm.doc.bank_party_name,
					}, {
						label: 'Party Type',
						fieldname: 'party_type',
						fieldtype: 'Link',
						options: "Party Type",
						reqd: 1,
						default: defalutValue,
						onchange: function(){
							dialog.fields_dict['party'].df.options = dialog.get_value('party_type');
							dialog.fields_dict['party'].refresh();
						}
					}, {
						label: 'Party',
						fieldname: 'party',
						fieldtype: 'Link',
						reqd: 1,
						default: frm.doc.party,
						options: defalutValue,
						onchange: async function(){
							party_name = await kefiya.iban_tools.getPartyName(dialog.get_value('party_type'), dialog.get_value('party'))
							dialog.set_value('party_name', party_name);
							dialog.fields_dict['party'].refresh();
						}
					}, {
						label: 'Party Name',
						fieldname: 'party_name',
						fieldtype: 'Data',
						default: party_name
						
					},  {
						label: 'Description',
						fieldname: 'description',
						fieldtype: 'Small Text',
						default: frm.doc.description,
						read_only: 1,
					}, {
						label: 'GL Account',
						fieldname: 'gl_account',
						fieldtype: 'Data',
						read_only: 1,
						hidden: 1,
						default: (
							frm.doc.payment_type == "Pay"
						) ? frm.doc.paid_from : frm.doc.paid_to
					}, {
						label: 'Bank Details',
						fieldtype: 'Section Break',
						description:(data.bankData.name)? 'The below data is fetched by using <a href="https://openiban.com/" target="_blank">OpenIban webservice</a>, please make sure that you validate the result manually or by using the validation service.': ''
					}, {
						label: 'Bank Name',
						fieldname: 'bank_name',
						fieldtype: 'Data',
						// read_only: 1,
						reqd: 1,
						default: data.bankData.name,
					}, {
						label: 'BIC',
						fieldname: 'bic',
						fieldtype: 'Data',
						// read_only: 1,
						reqd: 1,
						default: data.bankData.bic,
					}, {
						fieldname: 'col_break2',
						fieldtype: 'Column Break',
					}, {
						label: 'Bank Code',
						fieldname: 'bank_code',
						fieldtype: 'Data',
						// read_only: 1,
						reqd: 1,
						default: data.bankData.bankCode,
					}, ],
					primary_action_label: __('Submit'),
					primary_action: function(/* values */) {
						
						frm.doc.party =  dialog.get_value("party")
						frm.doc.party_type =  dialog.get_value("party_type")
						frm.doc.bank_party_name = dialog.get_value("sender")


						data.bankData.name =  dialog.get_value("bank_name")
						data.bankData.bankCode = dialog.get_value("bank_code")
						data.bankData.bic = dialog.get_value('bic')
						
						kefiya.iban_tools.createPartyBankAccount(frm.doc, data, callback);
						dialog.hide();
					},
				});
				dialog.show();
					
			} else {
				frappe.throw(__("Could not fetch bank details"));
			}
		});
	}
};
