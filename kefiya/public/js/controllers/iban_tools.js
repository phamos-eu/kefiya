// Copyright (c) 2019, jHetzer and contributors
// For license information, please see license.txt

frappe.provide("kefiya.iban_tools");

kefiya.iban_tools = {
	// Length of the IBAN per country, as listed in the SWIFT IBAN Registry
	ibanLengths: {
		15: ["NO"],
		16: ["BE"],
		18: ["DK", "FI", "FO", "GL", "NL"],
		19: ["MK", "SI"],
		20: ["AT", "BA", "EE", "KZ", "LT", "LU"],
		21: ["CH", "CR", "HR", "LI", "LV"],
		22: ["BG", "BH", "DE", "GB", "IE", "ME", "RS"],
		23: ["AE", "GI", "IL"],
		24: ["AD", "CZ", "ES", "MD", "PK", "RO", "SA", "SE", "SK", "TN"],
		25: ["PT"],
		26: ["IS", "TR"],
		27: ["FR", "GR", "IT", "MC", "MR", "SM"],
		28: ["AL", "AZ", "CY", "DO", "GT", "HU", "LB", "PL"],
		29: ["BR", "PS", "QA"],
		30: ["JO", "KW", "MU"],
		31: ["MT"],
	},

	// Whether value is an IBAN of a known country with a correct check sum.
	// Spaces are allowed, as IBANs are often written in groups of four.
	isValidIBANNumber: function(value) {
		const iban = String(value || "").replace(/\s+/g, "").toUpperCase();
		if (!/^[A-Z]{2}[0-9]{2}[A-Z0-9]+$/.test(iban)) {
			return false;
		}
		const country = iban.slice(0, 2);
		const length = Object.keys(kefiya.iban_tools.ibanLengths).find(
			(len) => kefiya.iban_tools.ibanLengths[len].includes(country)
		);
		if (Number(length) !== iban.length) {
			return false;
		}

		// ISO 13616: country code and check digits go to the end, every letter
		// becomes a number from 10 (A) to 35 (Z), and the whole number divided
		// by 97 leaves 1. The remainder is taken digit by digit, as the number
		// is too long for a JavaScript number.
		let remainder = 0;
		for (const char of iban.slice(4) + iban.slice(0, 4)) {
			const number = parseInt(char, 36);
			remainder = (remainder * (number < 10 ? 10 : 100) + number) % 97;
		}
		return remainder === 1;
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
			const data = {
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
						else if (party_type === "Employee"){
							resolve(r.message.employee_name);
						}
						else if (party_type === "Shareholder"){
							resolve(r.message.title);
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
