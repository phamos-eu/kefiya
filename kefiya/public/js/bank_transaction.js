frappe.ui.form.on("Bank Transaction Payments", {
    payment_entry: function(frm, cdt, cdn){
        let row = locals[cdt][cdn];
        frappe.db.get_doc(row.payment_document, row.payment_entry)
                .then(payment_entry => {
                    let allocated_amount = 0
                    if (row.payment_document == "Payment Entry"){
                        allocated_amount = payment_entry.total_allocated_amount
                    } else if (row.payment_document == "Journal Entry"){
                        allocated_amount = payment_entry.total_debit
                    } else if (row.payment_document == "Sales Invoice"){
                        allocated_amount = payment_entry.outstanding_amount
                    } else if (row.payment_document == "Purchase Invoice"){
                        allocated_amount = payment_entry.outstanding_amount
                    } else if (row.payment_document == "Expense Claim"){
                        allocated_amount = payment_entry.grand_total
                    }
                    
                    frappe.model.set_value(cdt, cdn, "allocated_amount", allocated_amount);
                })
    }
});